# Libra — กฎถาวร (อ่านก่อนแตะ Libra/KDP ทุกครั้ง)

อ่าน `memory.md` (ท้ายไฟล์ = ล่าสุด) ก่อนเริ่มงานเสมอ. กฎที่ห้ามละเมิด:

## 🚫 ห้าม republish เล่มที่เคย publish แล้ว (มี ASIN)
ทุกการ republish (เปลี่ยนหมวด/subtitle/description/ปก/เนื้อใน) = ส่งเล่มกลับเข้า Amazon content review ใหม่ = ทอยลูกเต๋า. **acuarela ถูก reject "disappointing customer experience" + หลุดจากร้าน (404) เมื่อ 11 ก.ค. 2026 จากการ republish เพื่อเปลี่ยนหมวดเท่านั้น.** ปกเคยผ่าน (3 ก.ค.) ≠ การันตีว่าจะผ่านอีก.
- Gate ในโค้ด: `scripts/kdp_action_executor.py::validate_action` refuse `category_update` ทุก listing ที่มี `asin` — ห้ามถอย gate นี้
- งานที่พับเก็บเพราะกฎนี้ (11 ก.ค.): subtitle rewrite 12 เล่ม, cover regen 39 เล่ม — ห้ามหยิบมาทำโดยไม่มีคำสั่งบุ๋ยชัดๆ

## 🔴 บัญชี KDP มี content block สะสม 2 ครั้ง — ปกป้องบัญชีมาก่อนทุกเล่ม
1. `high-protein-meal-plan-french` (นิช diet — NO-GO gate มีแล้ว)
2. `acuarela-para-principiantes-guia-paso-a-paso` (11 ก.ค. 2026 — บุ๋ยตัดสิน: ปล่อยตาย **ห้าม appeal / ห้าม resubmit**)

block ครั้งถัดไปเสี่ยงระดับปิดบัญชี. ห้ามทำอะไรที่เพิ่มโอกาสโดน review โดยไม่จำเป็น.

## ✅ เลนที่ปลอดภัย (ไม่ trigger content review)
- **Free promo / Countdown** — หน้า promotion-manager (`scripts/free_promo_auto.py`; โหมด manual: `--only <slug> --force --start YYYY-MM-DD --days N`, dry-run ก่อนเสมอ)
  - **กฎ pairing (14 ก.ค. 2026):** free promo ต้องมีคู่ช่องทางขยาย traffic เสมอ — ประกาศใน `data/promo_pairings.json` หรือมีคิวโพสต์ใน `data/reddit_promo_schedule.json` (executor refuse ถ้าไม่มี) เหตุผล: วัดจริง 17 โปรโม → 13 เล่มที่แจกเดี่ยวๆ ได้ 0 downloads และโควตาฟรีมีแค่ 5 วัน/เล่ม/เทอม เมื่อ executor schedule โปรโมสำเร็จจะเติมคิวเตือน Reddit ให้อัตโนมัติ
  - **ก่อนเสนอ/นัด promo เล่มไหน เช็คหน้า promotion-manager จริงก่อนเชื่อ listing.json** — เคยเจอเล่มที่โปรโมไปแล้ว 4-6 ก.ค. แต่ listing ไม่มีบันทึก (ai-creative-workbook-italian) ทำให้เกือบแจกซ้ำ
- **ราคา** — หน้า pricing ตรง (`scripts/set_price.py` มี SAFETY GATE royalty 35% ค้างระหว่างโปรโม — ห้าม publish ถ้า gate abort)
- **A+ Content** — ระบบแยก ไม่แตะตัวเล่ม
- **Ads / external traffic** — ไม่แตะตัวเล่ม (แต่ ads ติดเงื่อนไข checkpoint 31 ก.ค. + เพดานงบ)

## 📕 กฎเนื้อหา
- นิช visual (สอนวาด/ทำอาหาร/งานฝีมือ/คู่มือมีภาพประกอบ) **ต้องมีภาพสาธิตจริงในเนื้อ** ก่อน publish — text-only ในนิชภาพ = นิยาม "disappointing customer experience" ของ Amazon
- นิช diet/meal-plan = NO-GO ถาวร

## โหมดปัจจุบัน (11 ก.ค. 2026 →)
90-day profit mode: หยุดสร้างเล่มใหม่, cron หลัก = profit agent 10:15 (`--execute-actions`), เลน experiment = **free_promo + price_update** (category ปิดโดย gate). Checkpoint 31 ก.ค. = วันตัดสิน ads/paid promo.

## กฎ Autonomous Management (บุ๋ยยืนยัน 18 ก.ค. 2026)

**อะไรทำ auto ได้ให้ทำ แต่ต้องมั่นใจและเห็นผลจริง ห้ามเดา**

- Auto ได้เมื่อ input มาจากหลักฐานตรวจสอบได้ และผลลัพธ์มี `verified_state_change`, KDP/API response, report/transaction จริง หรือ external `post_url`/`post_id` ที่ตรวจย้อนกลับได้
- การคำนวณ, จัดอันดับ, forecast และข้อเสนอทำ auto ได้ แต่ต้องแยก verified fact ออกจาก inference และแสดง data freshness/confidence
- External action จะนับว่า `executed` ได้ต่อเมื่อมีหลักฐานผลลัพธ์จริง ห้ามนับ reminder, digest, planned queue, browser click หรือ process exit code อย่างเดียวเป็นความสำเร็จ
- ถ้าต้องเดา, ข้อมูล stale/incomplete, ติด OTP/CAPTCHA/login, หรือยืนยัน before/after ไม่ได้ → หยุดเป็น `manual_required`/`insufficient_data` และแจ้งบุ๋ย ห้ามฝืนทำ
- กฎความปลอดภัยบัญชี, no-paid policy, experiment cap และข้อห้าม republish มีอำนาจเหนือ automation เสมอ

### เลน price experiment (บุ๋ยอนุมัติ 11 ก.ค.)
- เกณฑ์เสนอ (proposer, deterministic): LIVE + KENP ≥50 (มีคนอ่าน KU จริง) + royalties ≤$1 + ราคาปัจจุบัน >$2.99 → ทดลอง $2.99, วัดผล 14 วัน (contribution delta)
- ราคาปัจจุบันอ่านจาก listing.price → fallback pricing-recommendation.json `recommended_price_usd`; ไม่มีทั้งคู่ = ข้าม
- **ห้ามคร่อมโปรโม**: มีโปรโมในหน้าต่างวัดผล 14 วัน = เลื่อน (one variable per window) + gate executor refuse ถ้าโปรโมคลุมวันนี้ (KDP ล็อก royalty 35% ระหว่างโปรโม — set_price มี abort gate ชั้นสอง)
- Band $2.99-9.99 (70%) เท่านั้น, ≤1 mutation/รอบ เหมือนเดิม
