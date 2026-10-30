"""Mini Brain - Level 3: agent LLM trả lời câu hỏi tiếng Việt về dữ liệu bảo hành.

LLM không tự tính toán. Nó gọi tool (SQL trên DuckDB, bộ phát hiện bất thường ở main.py),
nhận kết quả thật rồi mới viết câu trả lời -> ít bịa số hơn.

Chạy:
    uv run agent.py                                 # chạy vài câu hỏi demo
    uv run agent.py "Tháng 6 có gì bất thường?"     # hỏi câu riêng

Cần .env: OPENROUTER_API_KEY, OPENROUTER_URL, MODEL
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd
from openai import OpenAI, RateLimitError

from generate_data import DATA_PATH, generate
from main import check_cost_by_dealer, check_data_quality, check_volume_spike, drill_down

MAX_STEPS = 8  # chặn agent lặp vô hạn
MAX_RETRIES = 5  # model :free của OpenRouter hay bị 429, thử lại với backoff
MAX_ROWS = 50  # chặn kết quả SQL quá dài làm tràn context

SYSTEM_PROMPT = """Bạn là chuyên gia phân tích dữ liệu bảo hành xe điện. Trả lời bằng tiếng Việt, ngắn gọn.
Quy tắc:
- KHÔNG tự tính hay đoán số. Mọi con số phải lấy từ kết quả tool.
- Câu hỏi chung ("có gì bất thường?") -> gọi detect_anomalies trước, rồi dùng run_sql hoặc drill_down để kiểm chứng.
- Chỉ nêu những bất thường liên quan đúng phạm vi câu hỏi (tháng/linh kiện/đại lý được hỏi). Đừng liệt kê tất cả.
- Mỗi finding có evidence, trong đó thường đã có "Nguyên nhân". Chép lại nguyên nhân đó, không viết "chưa rõ" khi evidence đã nêu.
- Kết thúc bằng: kết luận, bằng chứng (số liệu), và nguyên nhân nghi ngờ.
- Nếu tool không cho thấy bất thường thì nói thẳng là không thấy.
Bảng `claims` (DuckDB): claim_id, date, dealer, vin, part, supplier, lot, cost_usd, odometer_km.
Dữ liệu cả năm 2025. part: battery, motor, inverter, brake, infotainment, charger."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "detect_anomalies",
            "description": "Chạy các bộ phát hiện bất thường (claim trùng, odometer sai, chi phí đại lý cao, claim tăng đột biến theo tháng kèm nguyên nhân). Trả về danh sách finding. Có thể lọc theo loại.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["all", "duplicate_claim", "invalid_value", "cost_anomaly", "volume_spike"],
                        "description": "Loại bất thường cần lấy, mặc định all",
                    },
                    "month": {
                        "type": "string",
                        "description": "YYYY-MM. Khi câu hỏi nhắc đến một tháng, PHẢI truyền (vd 'tháng 6' -> 2025-06). Chỉ trả về bất thường xảy ra trong tháng đó; claim trùng/odometer/chi phí đại lý là bất thường cả năm nên không có tháng.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": f"Chạy 1 câu SELECT DuckDB trên bảng claims để kiểm chứng/đào sâu. Tối đa {MAX_ROWS} dòng.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Câu SELECT hoặc WITH ... SELECT"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drill_down",
            "description": "Xác định phần claim tăng thêm của (part, supplier) trong 1 tháng tập trung ở lot hay dealer nào.",
            "parameters": {
                "type": "object",
                "properties": {
                    "part": {"type": "string"},
                    "supplier": {"type": "string"},
                    "month": {"type": "string", "description": "Định dạng YYYY-MM, ví dụ 2025-06"},
                },
                "required": ["part", "supplier", "month"],
            },
        },
    },
]


def load_env(path: Path = Path(__file__).parent / ".env") -> None:
    """Đọc KEY=VALUE từ .env vào os.environ (đủ dùng, khỏi thêm thư viện)."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


class Toolbox:
    """Các tool mà LLM được phép gọi. Giữ df & kết nối DuckDB dùng chung."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.con = duckdb.connect()  # in-memory
        self.con.register("claims", df)
        self._findings: list[dict] | None = None

    def detect_anomalies(self, kind: str = "all", month: str | None = None) -> list[dict]:
        if self._findings is None:  # tính 1 lần, các lần gọi sau dùng lại
            self._findings = check_data_quality(self.df) + check_cost_by_dealer(self.df) + check_volume_spike(self.df)
        # Lọc ở tool chứ không nhờ LLM lọc: model nhỏ hay liệt kê hết rồi bỏ sót đúng cái được hỏi.
        # Chỉ finding gắn với tháng (volume_spike) mới có scope.month; loại còn lại là cả năm.
        return [
            f
            for f in self._findings
            if (kind == "all" or f["type"] == kind) and (month is None or f["scope"].get("month") == month)
        ]

    def run_sql(self, query: str) -> list[dict]:
        # Chỉ cho đọc: LLM sinh SQL nên không tin tưởng tuyệt đối.
        if not re.match(r"^\s*(select|with)\b", query, re.I) or ";" in query.strip().rstrip(";"):
            raise ValueError("Chỉ cho phép đúng 1 câu SELECT/WITH")
        return self.con.execute(query).df().head(MAX_ROWS).to_dict("records")

    def drill_down(self, part: str, supplier: str, month: str) -> str:
        return drill_down(self.df, part, supplier, pd.Period(month, "M"))

    def call(self, name: str, args: dict):
        try:
            return getattr(self, name)(**args)
        except Exception as e:  # trả lỗi cho LLM để nó tự sửa (vd: SQL sai)
            return {"error": f"{type(e).__name__}: {e}"}


def chat(client: OpenAI, model: str, messages: list):
    for attempt in range(MAX_RETRIES):
        try:
            return client.chat.completions.create(model=model, messages=messages, tools=TOOLS).choices[0].message
        except RateLimitError:
            if attempt == MAX_RETRIES - 1:
                raise
            wait = 5 * 2**attempt
            print(f"   ⏳ 429 rate limit, thử lại sau {wait}s")
            time.sleep(wait)


def ask(client: OpenAI, model: str, tools: Toolbox, question: str) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
    for _ in range(MAX_STEPS):
        msg = chat(client, model, messages)
        messages.append(msg)
        if not msg.tool_calls:  # LLM không gọi tool nữa -> đây là câu trả lời cuối
            return msg.content
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            print(f"   🔧 {tc.function.name}({json.dumps(args, ensure_ascii=False)})")
            result = tools.call(tc.function.name, args)
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False, default=str)}
            )
    return "(Dừng: vượt quá số bước tối đa mà chưa có kết luận)"


DEMO_QUESTIONS = [
    "Tháng 6 có gì bất thường không? Nguyên nhân là gì?",
    "Có đại lý nào đáng ngờ về gian lận không? Chứng minh bằng số liệu.",
    "Tổng chi phí bảo hành của pin (battery) theo từng nhà cung cấp là bao nhiêu?",
]


def main():
    load_env()
    if not DATA_PATH.exists():
        DATA_PATH.parent.mkdir(exist_ok=True)
        generate().to_csv(DATA_PATH, index=False)
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])

    client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url=os.environ["OPENROUTER_URL"])
    model, tools = os.environ["MODEL"], Toolbox(df)

    for q in sys.argv[1:] or DEMO_QUESTIONS:
        print("=" * 80, f"\n❓ {q}\n", sep="")
        print(f"\n💡 {ask(client, model, tools, q)}\n")


if __name__ == "__main__":
    main()
