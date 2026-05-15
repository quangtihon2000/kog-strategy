# Per-Account Configuration — Design Documents

Bộ tài liệu này mô tả thiết kế chi tiết cho tính năng **per-account configuration** của KOG Strategy: cho phép từng MT5 account override input parameters của EA qua file JSON commit trong repo, deploy tự động qua CI/CD, và mở rộng `services/strategy-stats` để báo cáo P&L per-account cho cả 3 chiến lược.

## Vấn đề

Hiện tại mỗi EA (`ZoneSignalEA`, `CondeAutoEntryEA`, `GvfxSignalEA`) dùng một bộ `input` parameters chung — set qua MetaTrader Inputs dialog hoặc chart template, **không phân biệt account**.

Khi vận hành nhiều account cùng lúc (prop firm khác nhau, demo vs live, balance khác nhau), mỗi account cần tinh chỉnh **lot size, max positions, spread cap, magic number, TP/SL params** riêng. Cách duy nhất hiện nay là sửa chart template thủ công trên từng MT5 terminal:

- Không version control — không biết ai sửa gì khi nào.
- Không qua CI — không validate trước khi áp.
- Không audit trail — drift giữa các terminal.
- Không thể rollback nhanh khi config sai.

## Mục tiêu

1. **File config per-account** được commit vào repo dưới `strategies/{name}/config/accounts/{account}.json`.
2. **EA load tự động** lúc `OnInit`: override input parameters bằng giá trị trong JSON; thiếu file → dùng default đã compile.
3. **CI/CD validate + deploy**: thay đổi file config → pipeline kiểm tra schema → sync xuống MT5 terminal đúng VPS → restart EA để pick up.
4. **Multi-VPS ready**: pipeline biết terminal nào ở VPS nào, chỉ deploy đúng VPS đó.
5. **Stats per-account** cho cả 3 chiến lược (Zone đã có; Conde + Gvfx bổ sung).

## Quyết định đã chốt

| Quyết định | Lý do |
|---|---|
| 1 file JSON / 1 account | Đơn giản, dễ diff/review, đặt tên theo account ID = không trùng. |
| Override **toàn bộ** inputs + magic | Magic per-account để các EA cùng chiến lược trên 2 account không đụng nhau khi cùng VPS. |
| Mô hình 1 account / 1 terminal (như hiện tại) | Tránh phải route signal theo account trong cùng terminal. |
| Stats per-account cho cả 3 EA | Cùng pattern, code reuse từ `zone_account.html` template. |
| Junction (symlink) `MQL5/Files/{EA}/config` → repo | Rẻ nhất, không cần copy; `git pull` xong EA đọc được ngay. |
| Schema flat (key-value trong `inputs.*`) | MQL5 parser hand-rolled `StringFind`, không hỗ trợ nested tốt. |
| Không hot-reload v1 | Restart terminal sau deploy là đủ; đổi magic giữa chừng dễ orphan position. |

## Cấu trúc tài liệu

| File | Nội dung |
|---|---|
| [01-schema.md](./01-schema.md) | Schema JSON file, ví dụ, validation rules, forward-compat policy. |
| [02-ea-changes.md](./02-ea-changes.md) | Sửa code MQL5: shadow globals, `LoadAccountConfig`, helper JSON, rename `Inp*` → `g_cfg_*`. |
| [03-multi-vps.md](./03-multi-vps.md) | `deploy.json` thêm `vps` + `accounts`; workflow matrix theo VPS; runner label; risks. |
| [04-cd-pipeline.md](./04-cd-pipeline.md) | Junction config, `validate-account-configs.ps1`, `deploy-account-configs.ps1`, `_lib.ps1`, change detection. |
| [05-stats-service.md](./05-stats-service.md) | `aggregate_by_account` cho Conde + Gvfx; routes `/{strat}/account/{account}`; index migration. |
| [06-rollout-and-verification.md](./06-rollout-and-verification.md) | Order of implementation, end-to-end verification, risks & mitigations. |

## Trạng thái

Thiết kế — chưa code. Sau khi review, implementation theo thứ tự ở [06-rollout-and-verification.md](./06-rollout-and-verification.md).
