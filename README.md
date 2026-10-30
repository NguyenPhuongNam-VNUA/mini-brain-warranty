# Mini Brain: Phát hiện bất thường trong dữ liệu bảo hành xe điện

Bài luyện tập cho dạng bài toán **"brain / reasoning"** trên dữ liệu doanh nghiệp: hệ thống tự soi một lượng dữ liệu lớn, **tìm ra chỗ bất thường**, **giải thích vì sao** nó bất thường, và đưa ra kết quả ở dạng có cấu trúc để người (hoặc LLM) đọc và ra quyết định.

---

## Mục lục

1. [Bài toán là gì](#1-bài-toán-là-gì)
2. [Dữ liệu](#2-dữ-liệu)
3. [Các bất thường được cài sẵn ("đáp án")](#3-các-bất-thường-được-cài-sẵn-đáp-án)
4. [Kiến trúc tổng thể](#4-kiến-trúc-tổng-thể)
5. [Chi tiết từng bước và phương pháp](#5-chi-tiết-từng-bước-và-phương-pháp)
6. [Kết quả đầu ra](#6-kết-quả-đầu-ra)
7. [Công nghệ sử dụng](#7-công-nghệ-sử-dụng)
8. [Cách chạy](#8-cách-chạy)
9. [Hạn chế và bài học rút ra](#9-hạn-chế-và-bài-học-rút-ra)
10. [Lộ trình tiếp theo và bài tập](#10-lộ-trình-tiếp-theo-và-bài-tập)
11. [Thuật ngữ](#11-thuật-ngữ)

---

## 1. Bài toán là gì

### Bối cảnh

Một hãng xe điện bán xe kèm chế độ bảo hành. Khi xe hỏng, khách mang xe đến **đại lý (dealer)**. Đại lý sửa xong thì gửi về hãng một **yêu cầu bảo hành (claim)** để hãng thanh toán chi phí.

```
Khách hàng ──xe hỏng──▶ Đại lý ──sửa xe──▶ Gửi claim ──▶ Hãng xe thanh toán
                                               │
                                               ▼
                                   Hàng chục nghìn dòng / năm
                                   → không ai soi bằng mắt nổi
```

Hãng muốn biết sớm những chuyện như:

| Câu hỏi của doanh nghiệp | Tại sao quan trọng |
|---|---|
| Có linh kiện / nhà cung cấp nào đang lỗi hàng loạt không? | Phát hiện sớm thì triệu hồi (recall) sớm, đòi bồi thường nhà cung cấp, giảm rủi ro an toàn |
| Có đại lý nào khai khống chi phí không? | Gian lận làm thất thoát tiền |
| Có claim nào bị gửi trùng không? | Trả tiền hai lần cho một lần sửa |
| Dữ liệu có bị nhập sai không? | Dữ liệu sai thì mọi phân tích phía sau đều sai |

### Yêu cầu bài toán

> **Input:** file CSV chứa toàn bộ claim bảo hành trong năm 2025.
>
> **Output:** danh sách các bất thường. Mỗi bất thường có:
> - **loại** (trùng lặp, chi phí, số lượng, dữ liệu sai)
> - **phạm vi** (đại lý nào, linh kiện nào, tháng nào)
> - **mức kỳ vọng và mức thực tế**
> - **bằng chứng / nguyên nhân**, viết ra được thành câu

Điểm khác với một bài "lọc dữ liệu" bình thường: hệ thống **không được biết trước** bất thường nằm ở đâu. Nó phải tự so sánh, tự đặt ngưỡng, tự khoanh vùng nguyên nhân. Đây là phần "reasoning".

### Vì sao dùng dữ liệu giả?

Bài tập không có dữ liệu thật, nên [generate_data.py](generate_data.py) **tự sinh dữ liệu** và **cố tình cài vào 4 bất thường**. Vì biết trước đáp án, ta đo được hệ thống có tìm đúng, tìm đủ, có báo nhầm hay không. Khi gặp dữ liệu thật, cách làm vẫn giữ nguyên.

---

## 2. Dữ liệu

File: `data/warranty_claims.csv`, khoảng **20.680 dòng**, mỗi dòng là một claim.

| Cột | Ý nghĩa | Ví dụ |
|---|---|---|
| `claim_id` | Mã claim | `C000123` |
| `date` | Ngày gửi claim | `2025-06-14` |
| `dealer` | Đại lý sửa xe (20 đại lý: `DL01`–`DL20`) | `DL07` |
| `vin` | Số khung xe (định danh từng chiếc xe) | `VIN0012345` |
| `part` | Linh kiện hỏng | `battery`, `motor`, `inverter`, `brake`, `infotainment`, `charger` |
| `supplier` | Nhà cung cấp linh kiện | `SUP_A` … `SUP_H` |
| `lot` | Lô sản xuất của linh kiện (`LOT06` = sản xuất tháng 6) | `LOT06` |
| `cost_usd` | Chi phí sửa chữa | `210.50` |
| `odometer_km` | Số km xe đã chạy lúc hỏng | `15000` |

**Dữ liệu "bình thường" được sinh như thế nào** (các con số đều tự đặt cho hợp lý, không phải số liệu thật):

- **Chi phí gốc**, tức là sửa linh kiện này thường tốn bao nhiêu: pin 3000$, motor 1500$, inverter 1200$, sạc 400$, infotainment 300$, phanh 200$.
- **Chi phí thực tế của 1 claim** = chi phí gốc × hệ số ngẫu nhiên **log-normal** (σ = 0.2). Ngoài đời không lần sửa nào đúng y giá: lần chỉ thay má phanh, lần phải thay thêm đĩa. Hệ số này phần lớn nằm trong khoảng 0.82–1.22, tức dao động khoảng ±20%. Hệ số luôn dương nên không sinh ra chi phí âm. Ví dụ phanh: phần lớn claim nằm trong khoảng 164–244$.
- **Cơ cấu claim theo linh kiện**, tức là trong tổng số claim thì bao nhiêu % thuộc linh kiện nào (**không phải** % số xe bị hỏng): phanh 30% (~6.000 claim, vì là đồ hao mòn), infotainment 20%, pin 15%, sạc 15%, motor 10%, inverter 10% (~2.000 claim, vì bền hơn).
- Ngày, đại lý, số km được rải ngẫu nhiên đều.

**Các con số này liên quan gì đến bài toán?** Chúng tạo ra *mức bình thường*. Có mức bình thường thì mới định nghĩa được *bất thường*. Từng đặc điểm quyết định cách giải trong `main.py`:

| Đặc điểm của dữ liệu bình thường | Hệ quả cho cách giải |
|---|---|
| Mỗi linh kiện có giá rất khác nhau (200$ so với 3000$) | Phải **so chi phí trong cùng 1 linh kiện** (Bước 2). Nếu không, mọi claim pin đều trông "đắt bất thường" |
| Giá dao động tự nhiên ±20% | Không bắt từng claim lẻ: phanh 240$ vẫn bình thường. Phải **lấy trung bình nhiều claim** của 1 đại lý mới thấy DL07 (511$) lệch hẳn khỏi khoảng 160–240$ |
| Mỗi linh kiện có số claim khác nhau (6.000 so với 2.000) | Phải **so số lượng trong từng chuỗi riêng** (Bước 3) và **chuẩn hóa** `count_ratio` (Bước 5). Nếu không, phanh luôn trông "nhiều bất thường" |

> Nếu không có nhiễu, bài toán chỉ còn là "tìm số khác 200". Có nhiễu thì hệ thống phải **phân biệt được dao động tự nhiên với bất thường thật**. Đây là việc khó nhất của anomaly detection.

---

## 3. Các bất thường được cài sẵn ("đáp án")

| # | Tình huống ngoài đời | Cách cài trong dữ liệu | Loại bất thường |
|---|---|---|---|
| 1 | Lô inverter của **SUP_E** sản xuất tháng 6–7 bị lỗi | Thêm 600 claim inverter/SUP_E rơi vào tháng 6–7 | **Tăng đột biến số lượng** theo thời gian |
| 2 | Đại lý **DL07** khai khống tiền sửa phanh | Nhân chi phí phanh ở DL07 lên **×2.5** | **Giá trị lệch** so với nhóm ngang hàng |
| 3 | Đại lý **DL13** gửi một claim hai lần | Nhân bản 80 claim của DL13, chỉ đổi `claim_id` | **Trùng lặp** |
| 4 | Lỗi nhập liệu | 30 dòng có `odometer_km = -1` | **Giá trị không hợp lệ** |

Bốn loại này đại diện cho bốn họ bất thường hay gặp nhất trong dữ liệu doanh nghiệp. Mỗi họ cần một phương pháp khác nhau. Đó là lý do pipeline có nhiều bước.

---

## 4. Kiến trúc tổng thể

```
                         data/warranty_claims.csv
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────┐
│ LEVEL 1: LUẬT + THỐNG KÊ                                              │
│                                                                       │
│  [B1] check_data_quality      Luật cứng ─────────▶ trùng, odometer âm │
│  [B2] check_cost_by_dealer    Robust z-score  ───▶ DL07 phanh đắt     │
│        (so ngang hàng: dealer vs dealer, cùng linh kiện)              │
│  [B3] check_volume_spike      Robust z-score  ───▶ inverter tăng vọt  │
│        (so theo thời gian: tháng này vs các tháng khác)               │
│          └─▶ [B4] drill_down  Contribution analysis ─▶ "do LOT06,     │
│                                                    không do dealer"   │
└───────────────────────────────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────────────────────────────────────────┐
│ LEVEL 2: MACHINE LEARNING                                             │
│  [B5] check_isolation_forest  Isolation Forest (PyOD) ─▶ đối chiếu    │
└───────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                 Mọi kết quả → cùng 1 format JSON (finding)
                                    │
                 ┌──────────────────┴──────────────────┐
                 ▼                                     ▼
       outputs/findings.json               outputs/claims_by_month.png
     (Level 3: đưa cho LLM đọc)                  (cho người xem)
```

**Nguyên tắc thiết kế:** đi từ đơn giản đến phức tạp. Cái gì bắt được bằng luật thì dùng luật, vì luật nhanh, chính xác và dễ giải thích. Cái gì cần "so với bình thường" thì dùng thống kê. ML dùng để quét rộng và bắt những thứ chưa nghĩ ra luật.

---

## 5. Chi tiết từng bước và phương pháp

Toàn bộ nằm trong [main.py](main.py).

### Công cụ nền tảng: Robust z-score

Hầu hết các bước dùng chung một câu hỏi: **"giá trị này lạ đến mức nào so với nhóm của nó?"**

**z-score thông thường:**

$$z = \frac{x - \text{mean}}{\text{std}}$$

**Vấn đề:** chính điểm bất thường kéo `mean` lệch đi và làm `std` phình to. Kết quả là điểm bất thường trông "bớt lạ" hơn thực tế (hiệu ứng *masking*).

Ví dụ: chi phí phanh của 20 đại lý là `[200, 205, 198, ..., 511]`. Số 511 làm mean tăng và std tăng gấp nhiều lần, nên z của DL07 bị kéo xuống.

**Robust z-score** (hàm `robust_zscore`) thay mean bằng **median** và std bằng **MAD**:

$$\text{MAD} = \text{median}(|x_i - \text{median}(x)|)$$

$$z_{robust} = \frac{0.6745 \cdot (x - \text{median})}{\text{MAD}}$$

- Median và MAD gần như không bị ảnh hưởng bởi một vài điểm cực đoan.
- Hằng số `0.6745` quy đổi để z_robust cùng thang đo với z thường khi dữ liệu phân phối chuẩn.
- **Ngưỡng `Z_THRESHOLD = 3.5`**: ngưỡng khuyến nghị của Iglewicz & Hoaglin cho robust z-score. Lớn hơn 3.5 thì coi là bất thường.
- Nếu MAD = 0 (mọi giá trị giống nhau) thì trả về 0 để tránh chia cho 0.

### Bước 1: Data quality bằng luật cứng (`check_data_quality`)

**Ý tưởng:** có những thứ **sai về mặt định nghĩa**, không cần thống kê.

| Luật | Cách làm | Tại sao |
|---|---|---|
| Claim trùng | `df.duplicated(["vin", "part", "date", "dealer", "cost_usd"])` | Cùng xe, cùng linh kiện, cùng ngày, cùng đại lý, cùng số tiền mà khác `claim_id` thì gần như chắc chắn là gửi lặp |
| Odometer âm | `df.odometer_km < 0` | Số km không thể âm |

**Chú ý khi chọn khóa trùng:** nếu chỉ dùng `vin + part`, một chiếc xe hỏng phanh hai lần trong năm (hoàn toàn bình thường) sẽ bị báo nhầm. Khóa càng nhiều cột thì càng chắc chắn, nhưng có thể bỏ sót trường hợp gian lận tinh vi hơn (ví dụ đổi ngày đi một chút). Đây là đánh đổi giữa **precision** (báo cái nào đúng cái đó) và **recall** (không bỏ sót).

Kết quả được gom theo đại lý, kèm tổng số tiền bị trả thừa. Con số tiền giúp người đọc biết ngay mức độ nghiêm trọng.

### Bước 2: Chi phí bất thường theo đại lý (`check_cost_by_dealer`)

**Câu hỏi:** đại lý nào có chi phí sửa **một linh kiện** cao bất thường so với **các đại lý khác**?

**Phương pháp: so sánh ngang hàng (peer comparison).**

1. Gom nhóm theo `(part, dealer)` và tính chi phí trung bình. Ta được bảng 6 linh kiện × 20 đại lý.
2. **Trong từng linh kiện** (`groupby("part").transform(robust_zscore)`), tính robust z-score của 20 đại lý.
3. Đại lý nào có z > 3.5 thì báo.

**Vì sao phải so trong cùng một linh kiện:** pin 3000$ là bình thường, phanh 3000$ thì cực lạ. Nếu so tất cả với nhau, mọi claim pin sẽ bị báo và claim phanh đắt sẽ bị lẫn đi. **Chọn đúng nhóm để so sánh là quyết định quan trọng nhất trong anomaly detection.**

**Vì sao so trung bình theo đại lý thay vì từng claim:** một claim phanh 400$ có thể chỉ là ca sửa nặng. Nhưng **trung bình 292 claim** ở mức 511$ thì không thể là ngẫu nhiên. Gom nhóm giúp lọc nhiễu.

### Bước 3: Số lượng tăng đột biến theo thời gian (`check_volume_spike`)

**Câu hỏi:** linh kiện của nhà cung cấp nào có số claim **tháng này** cao bất thường so với **các tháng khác của chính nó**?

**Phương pháp: so sánh theo thời gian (temporal comparison).**

1. `monthly_counts`: đếm số claim theo `(part, supplier, month)`. Ta được 8 chuỗi thời gian (mỗi cặp part/supplier một chuỗi), mỗi chuỗi 12 điểm.
2. Tính robust z-score **trong từng chuỗi**: tháng này so với 12 tháng của chính chuỗi đó.
3. Tháng nào có z > 3.5 thì báo, và gọi tiếp Bước 4 để tìm nguyên nhân.

**Khác Bước 2 ở chỗ nào:**

| | Bước 2 | Bước 3 |
|---|---|---|
| So với ai | Các đại lý khác (ngang hàng) | Chính mình ở các tháng khác (lịch sử) |
| Bắt được | Một đối tượng lệch hẳn so với đám đông | Một thời điểm lệch hẳn so với thường lệ |

**Ví dụ thực tế: chuỗi inverter** (kết quả của `monthly_counts` + robust z-score, chạy trên dữ liệu đã sinh):

Số claim mỗi tháng:

| Supplier | T1 | T2 | T3 | T4 | T5 | T6 | T7 | T8 | T9 | T10 | T11 | T12 | Median |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SUP_D | 84 | 72 | 99 | 68 | 77 | 78 | 86 | 85 | 83 | 86 | 77 | 82 | 82.5 |
| SUP_E | 93 | 83 | 99 | 97 | 89 | **399** | **363** | 83 | 72 | 96 | 85 | 80 | 91 |

Robust z-score (tính riêng trong từng chuỗi, ngưỡng 3.5):

| Supplier | T1 | T2 | T3 | T4 | T5 | T6 | T7 | T8 | T9 | T10 | T11 | T12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SUP_D | 0.3 | -1.8 | 2.8 | -2.4 | -0.9 | -0.8 | 0.6 | 0.4 | 0.1 | 0.6 | -0.9 | -0.1 |
| SUP_E | 0.2 | -0.7 | 0.7 | 0.5 | -0.2 | **26.0** | **22.9** | -0.7 | -1.6 | 0.4 | -0.5 | -0.9 |

Cách đọc:
- **SUP_E tháng 6–7** có z = 26.0 và 22.9, vượt xa ngưỡng 3.5. Số claim gấp khoảng 4 lần mức thường (399 so với 91), nên hệ thống báo bất thường.
- **SUP_D tháng 3** có z = 2.8 (99 claim so với median 82.5). Con số này cao nhưng **vẫn dưới ngưỡng**, là dao động ngẫu nhiên tự nhiên nên không báo. Ví dụ này cho thấy vai trò của ngưỡng: hạ `Z_THRESHOLD` xuống 2.5 thì SUP_D tháng 3 sẽ thành **báo nhầm** (false positive).

**Vì sao gom theo `(part, supplier)` chứ không theo `part`:** inverter có 2 nhà cung cấp là SUP_D và SUP_E. Nếu gộp chung, phần tăng của SUP_E bị "pha loãng" bởi SUP_D bình thường.

### Bước 4: Truy tìm nguyên nhân (`drill_down`)

Đây là phần **"reasoning"** rõ nhất. Biết "inverter/SUP_E tháng 6 tăng 4.4 lần" mới chỉ là **triệu chứng**. Người ra quyết định cần biết **nguyên nhân** để hành động đúng:

- Nếu tăng **tập trung ở một lô sản xuất** thì lỗi do nhà máy hoặc nhà cung cấp. Hành động: recall lô đó, làm việc với SUP_E.
- Nếu tăng **tập trung ở một đại lý** thì nghi đại lý đó gian lận hoặc chẩn đoán sai. Hành động: audit đại lý.
- Nếu **rải đều mọi nơi** thì có thể do yếu tố chung như thời tiết hay một bản cập nhật phần mềm.

**Phương pháp: contribution analysis (phân tích đóng góp).**

Với mỗi chiều (`lot`, `dealer`):

1. **Thực tế** (`actual`): số claim của từng giá trị trong tháng bất thường. Ví dụ LOT06: 399.
2. **Mức thường** (`baseline`): số claim trung bình mỗi tháng của giá trị đó ở **các tháng còn lại**.
3. **Phần dư** (`excess`) = thực tế − mức thường, cắt về 0 nếu âm.
4. Giá trị có phần dư lớn nhất chiếm bao nhiêu % tổng phần dư?
   - ≥ 50% (`top_share`): **"tập trung ở ..."**, tức nguyên nhân nằm ở chiều này.
   - < 50%: **"rải đều theo ..."**, tức chiều này không phải nguyên nhân.

**Kết quả thực tế:**
```
tập trung ở lot=LOT06 (100% phần tăng); rải đều theo dealer (lớn nhất DL16 chỉ 10%)
```
Từ đó suy ra: lỗi ở **lô sản xuất**, không phải ở đại lý. Kết luận này khớp với đáp án.

> Chiều "không phải nguyên nhân" cũng có giá trị như chiều "là nguyên nhân". Câu "rải đều theo dealer" giúp loại trừ giả thuyết gian lận của đại lý.

### Bước 5 (Level 2): Isolation Forest (`check_isolation_forest`)

**Ý tưởng:** các bước trên đều do **người nghĩ ra luật** (so cái gì với cái gì). ML không giám sát (unsupervised) cho phép mô hình **tự tìm** điểm "khác đám đông" mà không cần nhãn và không cần định nghĩa trước.

**Isolation Forest hoạt động thế nào:**
- Xây nhiều cây quyết định ngẫu nhiên. Mỗi lần chọn ngẫu nhiên một feature và một ngưỡng cắt để chia dữ liệu.
- Điểm bất thường nằm xa đám đông nên **bị cô lập chỉ sau vài lần cắt**, tức là đường đi trong cây ngắn.
- Điểm bình thường nằm giữa đám đông nên cần **rất nhiều lần cắt** mới tách riêng được.
- **Đường đi trung bình càng ngắn thì càng bất thường**, và anomaly score càng cao.

**Dữ liệu đưa vào mô hình:**
- Mỗi điểm = một bộ `(dealer, part, month)`, khoảng 1.440 điểm.
- Hai feature, đều **chuẩn hóa theo linh kiện** để pin và phanh so được với nhau:
  - `cost_ratio` = chi phí TB / median chi phí TB của linh kiện đó
  - `count_ratio` = số claim / median số claim của linh kiện đó
- `contamination=0.02`: giả định khoảng 2% dữ liệu là bất thường. Tham số này quyết định ngưỡng gắn nhãn `is_anomaly`.

**Kết quả:** top 10 đều là **DL07/brake**. Mô hình tự phát hiện ra bất thường số 2 mà không cần ai bảo phải so chi phí giữa các đại lý.

**Nhưng nó KHÔNG bắt được lỗi inverter.** Xem giải thích ở [mục 9](#9-hạn-chế-và-bài-học-rút-ra).

### Format kết quả thống nhất (`finding`)

Mọi bước đều trả về cùng một cấu trúc:

```json
{
  "type": "volume_spike",
  "scope": {"part": "inverter", "supplier": "SUP_E", "month": "2025-06"},
  "metric": "n_claims",
  "expected": 91,
  "actual": 399,
  "evidence": "inverter/SUP_E tháng 2025-06: 399 claim, gấp 4.4 lần mức thường (91). z=26.0. Nguyên nhân: tập trung ở lot=LOT06 (100% phần tăng); rải đều theo dealer (lớn nhất DL16 chỉ 10%)"
}
```

**Vì sao quan trọng:**
- Có `expected` và `actual` thì người đọc thấy ngay mức độ lệch.
- Có `evidence` bằng câu chữ thì giải thích được (*explainability*). Doanh nghiệp không hành động dựa trên câu "mô hình bảo thế".
- Có format máy đọc được thì ở **Level 3**, LLM đọc JSON này để viết báo cáo hoặc trả lời câu hỏi mà không phải tự tính toán. Nhờ vậy LLM ít bịa số hơn.

---

## 6. Kết quả đầu ra

### Console

```
LEVEL 1 - THỐNG KÊ & LUẬT: tìm thấy 5 bất thường

[1] duplicate_claim  {'dealer': 'DL13'}
    80 claim trùng, tổng 69,325 USD bị trả thừa.
[2] invalid_value  {'column': 'odometer_km'}
    30 dòng có odometer < 0.
[3] cost_anomaly  {'dealer': 'DL07', 'part': 'brake'}
    Chi phí TB sửa brake ở DL07 = 511$, gấp 2.5 lần median các đại lý khác (204$). z=116.5
[4] volume_spike  {'part': 'inverter', 'supplier': 'SUP_E', 'month': '2025-06'}
    ... gấp 4.4 lần mức thường. Nguyên nhân: tập trung ở lot=LOT06 (100% phần tăng); rải đều theo dealer
[5] volume_spike  {'part': 'inverter', 'supplier': 'SUP_E', 'month': '2025-07'}
    ... gấp 4.0 lần mức thường. Nguyên nhân: tập trung ở lot=LOT07 (100% phần tăng); rải đều theo dealer

LEVEL 2 - ISOLATION FOREST: top 10 đều là DL07 / brake
```

### Đối chiếu với đáp án

| Bất thường cài sẵn | Tìm thấy? | Bởi bước nào | Có nguyên nhân? |
|---|---|---|---|
| 1. Inverter SUP_E lô 06–07 | ✅ | B3 + B4 | ✅ LOT06/LOT07, không do dealer |
| 2. DL07 phanh ×2.5 | ✅ | B2 và B5 | ✅ gấp 2.5 lần |
| 3. DL13 claim trùng | ✅ | B1 | ✅ 80 claim, số tiền |
| 4. Odometer âm | ✅ | B1 | ✅ 30 dòng |
| Báo nhầm (false positive) | **0** | | |

### File

- `outputs/findings.json`: toàn bộ bất thường dạng JSON.
- `outputs/claims_by_month.png`: biểu đồ số claim theo tháng của từng part/supplier. Đường inverter/SUP_E được tô đậm và thấy rõ đỉnh ở tháng 6–7.

---

## 7. Công nghệ sử dụng

| Thư viện | Dùng để làm gì trong bài |
|---|---|
| **pandas** | Đọc CSV, `groupby` / `agg` / `transform` để gom nhóm và tính z-score trong từng nhóm, `duplicated` để tìm trùng, `to_period("M")` để gom theo tháng |
| **numpy** | Sinh số ngẫu nhiên khi tạo dữ liệu (`default_rng`, `lognormal`) |
| **PyOD** | Thư viện anomaly detection, có hơn 40 thuật toán dùng chung một API. Bài này dùng `IForest`. Thử thuật toán khác chỉ cần đổi một dòng import (`LOF`, `ECOD`, `AutoEncoder`...) |
| **matplotlib** | Vẽ biểu đồ chuỗi thời gian (backend `Agg` để lưu file, không mở cửa sổ) |
| **duckdb** | *Chưa dùng.* Chuẩn bị cho Level 3: chạy SQL trực tiếp trên CSV hoặc DataFrame, làm tool cho LLM |

Cấu trúc thư mục:

```
reasoning/
├── generate_data.py     # Sinh dữ liệu giả + cài 4 bất thường ("đề thi")
├── main.py              # Pipeline phát hiện bất thường ("bài làm")
├── data/
│   └── warranty_claims.csv
└── outputs/
    ├── findings.json
    └── claims_by_month.png
```

---

## 8. Cách chạy

```bash
# Cài thư viện (nếu chưa)
uv add pandas numpy matplotlib pyod duckdb

# (Tuỳ chọn) Sinh lại dữ liệu. main.py tự sinh nếu chưa có file
uv run generate_data.py

# Chạy pipeline
uv run main.py
```

Muốn thử nghiệm thì thay đổi:
- `Z_THRESHOLD` trong [main.py](main.py): giảm xuống thì báo nhiều hơn (dễ báo nhầm), tăng lên thì báo ít hơn (dễ bỏ sót).
- `top_share` trong `drill_down`: ngưỡng để coi là "tập trung".
- `contamination` trong `IForest`: tỉ lệ dữ liệu mô hình giả định là bất thường.
- Hệ số `× 2.5` hoặc số claim `600` trong [generate_data.py](generate_data.py): làm bất thường "nhẹ" đi xem đến mức nào thì hệ thống bắt không nổi.

---

## 9. Hạn chế và bài học rút ra

### Bài học 1: ML không thay được việc chọn đúng góc nhìn

Isolation Forest **bỏ sót lỗi inverter**. Nguyên nhân là mô hình xét từng ô `(dealer, part, month)`, trong khi 600 claim lỗi bị **chia đều cho 20 đại lý × 2 tháng**. Mỗi ô chỉ tăng khoảng 15 claim, không đủ nổi bật. Ở góc nhìn `(part, supplier, month)` thì phần tăng lại rất rõ (z = 26).

Vì vậy:
- **Mức độ gom nhóm (granularity) quyết định cái gì nhìn thấy được.** Gom quá nhỏ thì tín hiệu bị loãng. Gom quá to thì tín hiệu bị lẫn.
- Hệ thống thật thường **chạy nhiều góc nhìn song song**, kết hợp luật, thống kê và ML, chứ không dựa vào một mô hình duy nhất.

### Bài học 2: Bất thường chưa chắc là lỗi

Hệ thống chỉ nói "cái này khác thường". Nó **không** nói "cái này sai". Ví dụ DL07 có thể đắt thật vì ở vùng có giá nhân công cao. Đầu ra của hệ thống là **danh sách để con người kiểm tra**, không phải kết luận cuối cùng. Vì thế `evidence` phải rõ ràng.

### Hạn chế của cách làm hiện tại

| Hạn chế | Hướng cải thiện |
|---|---|
| Robust z-score với 12 tháng là ít điểm, nhạy với nhiễu | Dữ liệu nhiều năm, dùng STL decomposition để tách mùa vụ |
| Không xử lý tính mùa vụ (ví dụ mùa mưa hỏng nhiều hơn) | So với cùng kỳ năm trước, hoặc dùng mô hình Prophet |
| Chưa tính số xe đang lưu hành (càng bán nhiều xe thì càng nhiều claim là bình thường) | Chuẩn hóa thành **tỉ lệ claim / 1000 xe** |
| Drill-down chỉ xét từng chiều riêng lẻ | Xét tổ hợp nhiều chiều (ví dụ lot × dealer), các thuật toán như Adtributor, iDice |
| Khóa trùng cứng, bỏ sót trùng "gần giống" | Fuzzy matching (ngày lệch ±3 ngày, số tiền lệch < 5%) |
| Chưa có odometer thấp bất thường (hỏng quá sớm) | Phân tích theo km / tuổi xe (survival analysis, Weibull) |

---

## 10. Lộ trình tiếp theo và bài tập

### Bài tập

1. **Thêm bất thường mới:** cài vào [generate_data.py](generate_data.py) tình huống *pin của SUP_B hỏng sớm* (`odometer_km` thấp bất thường), rồi viết hàm phát hiện trong [main.py](main.py).
2. **Sửa Isolation Forest để bắt được inverter:** thử gom theo `(part, supplier, month)` hoặc thêm feature mới. So sánh kết quả.
3. **Thử thuật toán khác của PyOD:** `LOF`, `ECOD`, `KNN`. Thuật toán nào bắt tốt nhất, thuật toán nào báo nhầm nhiều nhất?
4. **Đo độ nhạy:** giảm hệ số DL07 từ ×2.5 xuống ×1.5, ×1.2, ×1.1. Đến mức nào thì hệ thống không còn bắt được?
5. **Chuẩn hóa theo số xe:** giả lập thêm số xe bán ra theo tháng và tính tỉ lệ claim thay cho số đếm.

### Level 3: Agent "Brain" với LLM

Mục tiêu: người dùng hỏi bằng tiếng Việt, hệ thống tự phân tích và trả lời có bằng chứng.

```
"Tháng 6 có gì bất thường?"
        │
        ▼
   ┌─────────┐   gọi tool    ┌──────────────────────────────────────────┐
   │   LLM   │ ────────────▶ │ run_sql(query)          ← DuckDB         │
   │ (agent) │ ◀──────────── │ detect_anomalies(...)   ← main.py        │
   └─────────┘   kết quả     │ drill_down(...)         ← main.py        │
        │                    └──────────────────────────────────────────┘
        ▼
"Tháng 6 inverter của SUP_E tăng 4.4 lần (399 so với 91 claim),
 tập trung toàn bộ ở LOT06, không liên quan đại lý → nghi lỗi lô sản xuất.
 SQL đã chạy: SELECT ..."
```

Kiến thức cần học: **tool calling / function calling**, **text-to-SQL**, pattern **ReAct**, framework **LangGraph** (hoặc tự viết vòng lặp agent).

### Level 4: Kết nối OCR

Đại lý gửi kèm **ảnh hóa đơn sửa chữa**. Hệ thống OCR ảnh (PaddleOCR, VietOCR hoặc Vision-Language Model) thành JSON, rồi đối chiếu số tiền với `cost_usd` trong database. Chỗ nào lệch thì báo. Level này nối bài toán **OCR chứng từ** với bài toán **brain**.

---

## 11. Thuật ngữ

| Thuật ngữ | Giải thích |
|---|---|
| **Anomaly / Outlier** | Điểm dữ liệu khác biệt đáng kể so với phần còn lại |
| **Robust statistics** | Thống kê ít bị ảnh hưởng bởi outlier (median, MAD thay cho mean, std) |
| **MAD** | Median Absolute Deviation: median của khoảng cách từ mỗi điểm đến median |
| **Masking** | Hiện tượng outlier làm lệch mean/std, khiến chính nó trông bớt bất thường |
| **Peer comparison** | So một đối tượng với các đối tượng cùng loại (đại lý vs đại lý) |
| **Temporal comparison** | So một thời điểm với lịch sử của chính nó |
| **Granularity** | Mức độ chi tiết khi gom nhóm (theo ngày/tháng, theo đại lý/toàn quốc) |
| **Drill-down** | Chia nhỏ một con số tổng theo các chiều để xem nó đến từ đâu |
| **Contribution analysis** | Đo mỗi thành phần đóng góp bao nhiêu % vào một sự thay đổi |
| **Root cause analysis** | Phân tích tìm nguyên nhân gốc của một hiện tượng |
| **Unsupervised learning** | Học không cần nhãn đúng/sai |
| **Isolation Forest** | Thuật toán anomaly detection: điểm càng dễ bị cô lập bằng các lần cắt ngẫu nhiên thì càng bất thường |
| **Contamination** | Tỉ lệ dữ liệu giả định là bất thường, dùng để đặt ngưỡng |
| **Precision / Recall** | Precision: trong số đã báo, bao nhiêu % đúng. Recall: trong số bất thường thật, bắt được bao nhiêu % |
| **False positive** | Báo nhầm: bình thường mà bị báo là bất thường |
| **Explainability** | Khả năng giải thích vì sao hệ thống đưa ra kết luận |
| **Tool calling** | LLM tự quyết định gọi hàm nào, với tham số nào, rồi đọc kết quả |
| **Agent** | Hệ thống LLM lặp đi lặp lại: suy nghĩ, gọi tool, đọc kết quả, cho đến khi trả lời được |
