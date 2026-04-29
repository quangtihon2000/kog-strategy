# Quy tắc chọn model Claude (Opus / Sonnet / Haiku)

## Tóm tắt nhanh

| Model | Định vị | Model ID |
|-------|---------|----------|
| **Opus 4.7** | Mạnh nhất — lập luận phức tạp, agent dài hơi | `claude-opus-4-7` |
| **Sonnet 4.6** | "Daily driver" — cân bằng tốc độ + trí thông minh | `claude-sonnet-4-6` |
| **Haiku 4.5** | Nhanh & rẻ — khối lượng lớn, độ trễ thấp | `claude-haiku-4-5-20251001` |

---

## Rules — Khi nào dùng Opus

- Tác vụ **agent tự chủ chạy nhiều giờ** (autonomous coding, long-horizon planning).
- **Refactor lớn** xuyên nhiều file, thiết kế kiến trúc, debug bug khó.
- Lập luận nhiều bước, toán/khoa học, phân tích pháp lý/tài chính chuyên sâu.
- Khi **độ chính xác > chi phí và tốc độ**.
- Khi Sonnet đã thử nhưng kết quả không đủ tốt.

## Rules — Khi nào dùng Sonnet

- **Mặc định cho phần lớn coding tasks**: viết feature, sửa bug vừa, code review.
- Chatbot sản xuất, RAG, phân tích dữ liệu, tạo nội dung dài.
- Tool use / function calling thông thường, agent ngắn-trung hạn.
- Khi cần **chất lượng cao nhưng chi phí hợp lý** — đây là điểm sweet spot.

## Rules — Khi nào dùng Haiku

- **Real-time / low-latency**: chat UI, autocomplete, voice agent.
- **Khối lượng lớn**: phân loại, gắn nhãn, trích xuất, moderation, tóm tắt ngắn.
- **Sub-agent** trong hệ thống đa-agent (orchestrator dùng Sonnet/Opus, worker dùng Haiku).
- Prototype nhanh, batch processing tiết kiệm chi phí.

---

## Rules tổng quát (decision flow)

1. **Bắt đầu từ Sonnet** — đây là default tốt nhất cho 80% use case.
2. **Hạ xuống Haiku** nếu: tác vụ đơn giản, lặp lại, cần latency thấp hoặc throughput cao.
3. **Nâng lên Opus** chỉ khi Sonnet thất bại rõ rệt ở: lập luận đa bước, agentic loop dài, hoặc tác vụ critical cần độ chính xác cao nhất.
4. **Pattern phối hợp**: Opus/Sonnet làm "planner" → Haiku làm "executor" cho các bước con.
5. **Đo lường trước khi chọn**: chạy eval trên tác vụ thực; đừng chọn theo cảm tính.

## Tradeoff cần nhớ

- Opus: thông minh nhất nhưng **chậm + đắt nhất** → đừng dùng cho tác vụ Sonnet làm tốt.
- Sonnet: chất lượng gần Opus với chi phí thấp hơn nhiều → ưu tiên mặc định.
- Haiku: rẻ + nhanh nhất nhưng **giảm khả năng lập luận sâu** → không hợp với agentic tasks phức tạp.

## Trong Claude Code

- Đổi model trong session: lệnh `/model`.
- **Fast mode** (`/fast`): chỉ dành cho Opus 4.6, output nhanh hơn nhưng vẫn là Opus (không tự hạ cấp).
- Có thể đặt model mặc định trong `~/.claude/settings.json`.

---

**Nguồn tham khảo:**
- [Choosing a model — Anthropic docs](https://docs.claude.com/en/docs/about-claude/models/choosing-a-model)
- [Models overview](https://docs.claude.com/en/docs/about-claude/models/overview)
