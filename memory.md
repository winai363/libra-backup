
## 2026-07-02 — Audit ระบบเทียบมาตรฐาน KDP 2026 + แก้ 6 จุด (commit e8c31e6) 🔍

บุ๋ยสั่งตรวจว่า Libra สร้าง KDP ได้มาตรฐาน + เลือกหัวข้อขายได้ + SEO/กลยุทธ์ตรงหลัก 2026 ไหม. รีเสิร์ชกฎ 2026 จากแหล่ง official แล้วเทียบทั้งระบบ.

**ผ่านอยู่แล้ว:** AI disclosure ติ๊กถูกต้อง, cap 1 เล่ม/วัน (<3 ของ Amazon), keyword sanitizer ครบคำต้องห้าม, description HTML ใช้ tag ที่ KDP รองรับ, category ใช้ tree จริง 2,419 leaves + resolver, ราคา ebook clamp $2.99-9.99 (70%), learning loop ขายจริง→topic_scout ทำงาน, ADHD series ผูก series id + เข้า Select ครบ 3 เล่ม. Paperback ไม่เคยอัปขาย (PDF ใช้เป็นด่าน QA เท่านั้น) เลยไม่โดนกฎ royalty ใหม่ มิ.ย. 2025.

**แก้วันนี้ (root cause ขาย≈0 = ด่านวัดแค่ "ช่องว่าง" ไม่เคยวัด "มีคนซื้อจริง"):**
1. `market_intelligence.py` — demand rubric อิง BSR จริง (16-20=มี 3+ เล่มเทียบเคียง BSR<100k; ตลาดเล็กใช้เกณฑ์ครึ่งเดียว) + **DEMAND_MIN=8 hard gate ตัวที่ 3** (เดิมมีแค่ competition/lang_sat → เจอ blue ocean ที่ไม่มีปลา)
2. `topic_scout._opportunity_score` — บวกคะแนน expected_monthly_royalty (cap $50 → +10 แต้ม) ตามกฎ unit economics ของบุ๋ย
3. `auto-generate.sh` — **ปิด ungated fallback**: scout หา GO topic ไม่ได้ = ข้ามวัน (แจ้ง Telegram) ไม่ปล่อย writer เลือกหัวข้อเองแบบไร้ด่าน (ที่มาของเล่มเจนเนอริกอย่าง Advanced Python orphan) + retry writer ต้องส่ง $TOPIC_ARG เดิม
4. `metadata_polish.sanitize_keywords` — ลิมิต 50 ต่อ keyword นับเป็น **UTF-8 ไบต์** (ตัวมี accent = 2 ไบต์; เกิน = Amazon เมินทั้งช่อง) ตัดที่ word boundary
5. `quality_gate` — warning ใหม่ 2 ตัว: title+subtitle รวม >200 ตัวอักษร, keyword >50 ไบต์
6. Prompt writer + seo_optimizer — เพิ่มกฎ title+subtitle<200 รวม และ keyword ~45 ตัวอักษรสำหรับภาษามี accent

**แก้ข้อมูลจริง:** เขียน keyword ที่เกินไบต์ใหม่เป็นวลีสั้นธรรมชาติ 11 เล่ม (listing.json) → reupload metadata สำเร็จ **9/9 เล่ม LIVE** (ทดสอบ 1 + batch 8; อีก 2 เล่มไม่ LIVE ไฟล์แก้รอไว้แล้ว). เคลียร์ blocker ค้าง localized_taxonomy (senior-smartphone-french ขึ้น LIVE แล้ว 14:15, health ok blockers=0).

**ค้าง/ข้อเสนอต่อบุ๋ย:** (1) หนังสือ ~36 เล่ม LIVE ไม่ได้เข้า KDP Select เลย = ปิดประตู KU เอง — ควรเข้า Select 90 วันแรก (เล่มไม่ได้ขายที่อื่น exclusivity ไม่มีต้นทุน) มี kdp_enroll_v2.py พร้อม รอบุ๋ยอนุมัติ; (2) title+subtitle >200 ตัวอักษร 12 เล่ม LIVE — แก้ต้องเขียน subtitle ใหม่ (LLM) รออนุมัติ; (3) A+ Content ยังไม่ทำ (+5-12% conversion, ฟรี); (4) Amazon Ads ควรลองเฉพาะเล่มที่ขายแล้ว (easy-taxes-self-employed-spain) ~$5/วัน — ติดเพดาน ฿300/วัน ต้องบุ๋ยตัดสิน.

## 2026-07-02 — แก้เล่มภาษาอื่นล้มอัป KDP (localized categories → "Save and Continue" timeout) 🐞

**อาการ:** senior-smartphone-french (Guide Facile du Smartphone pour Seniors) fail อัป 2 รอบ (02:32, 06:32) — `Locator.click Timeout waiting for get_by_text("Save and Continue")` + category_health BLOCKER `localized_taxonomy`.
**สาเหตุ (root):** `gpt_fallback_writer.py` step3_write_listing (บรรทัด ~438) สั่ง GPT ทำ category paths "in the target language/marketplace" → เล่มฝรั่งเศสได้หมวดฝรั่งเศส ("Informatique et Internet > Outils et références", "Vie pratique > Guides pour seniors"). **KDP category picker เป็นอังกฤษล้วน** → uploader คลิกหมวดไม่เจอ → ค้างจนหมดเวลา. `category_resolver` แก้ไม่ได้ (จับคู่ leaf อังกฤษเท่านั้น; ฝรั่งเศส score<min → fallback คืนค่าเดิม). เล่มนี้ไม่มี seo-analysis.json = seo ไม่ adopt หมวดใหม่.
**แก้ (A) เล่มนี้:** set หมวดอังกฤษจริง 3 ตัว (Computers & Technology > Hardware > Mobile Devices / Tech Culture & Computer Literacy > General / > Reference — ทั้งหมดเป็น leaf จริงใน kdp_category_tree.json), clear kdp_error, status=ready. category_health_manager PASS (blockers 0, language_findings ว่าง, warnings 27→25).
**แก้ (B) กันซ้ำ:** `gpt_fallback_writer.py:438` เปลี่ยน prompt → บังคับ category paths **IN ENGLISH** (Amazon browse names) แม้เล่มเป็นภาษาอื่น ตรงกับ `_categories_rule` ใน seo_optimizer.py:90.
**ค้าง:** queue.txt ยังมี senior-smartphone-french; process_kdp_queue.sh (cron 02:30/06:30) จะอัปใหม่อัตโนมัติด้วยหมวดที่แก้ (daily new-title cap วันนี้ยังว่าง). เช็ค queue_log.txt ว่าขึ้น LIVE.

## 2026-07-01 — แก้ Libra ไม่โพส KDP (คิวว่าง + เล่มตกด่านลิงก์อ้างอิง)
**อาการ:** วันนี้ไม่มีเล่มขึ้น KDP. **สาเหตุ:** ระบบไม่ได้พัง — cron ครบ แต่ (1) queue.txt ว่าง (เล่ม 30 มิ.ย. ขึ้นสำเร็จหมดแล้ว) และ (2) เล่มที่ auto-generate สร้างตี 1 (guia-completa-modelo-130-autonomos-espana-2026) ตกด่าน quality_gate: "Only 7 valid reference URLs found; minimum is 8" — repair_links ตัด dead link (factuchat.es 404 = reference #2) ออก 1 อัน เหลือ 7/8 เลยไม่เข้าคิว.
**แก้ (A):** แทน reference #2 ด้วย https://www.declarando.es/modelo-130 (ทดสอบ 200 ด้วยวิธีเดียวกับ gate) → rebuild EPUB (pandoc cmd เดียวกับ repair_links._rebuild_epub) + force regen PDF (ลบ paperback.pdf เก่าก่อน ไม่งั้น API ตอบ "already exists") → quality_gate PASS → reset status=ready → เข้า queue.txt → รัน process_kdp_queue.sh → **Published สำเร็จ (kdp_book_id AHPMN3H21KVF8, $2.99)**.
**แก้ (B) กันซ้ำ:** gpt_fallback_writer.py ให้ research 12-15 sources + ใส่ references ≥10 (buffer เผื่อ dead-link โดนตัด) — ไม่ไปลด MIN_REFERENCES=8 ที่ gate. commit cebfcf9 push backup แล้ว.
**Orphan ai-workflow-planner-pt (จบแล้ว):** เคย status=ready ตั้งแต่ 12 มิ.ย. ไม่เคยเข้าคิวเพราะไม่มี PDF paperback. ตรวจแล้ว PDF สร้างได้/ลิงก์ครบ/QA เนื้อหาผ่าน **แต่ AI editorial board ตีตก** (factual_reliability 6<7, citation_quality 6<7, originality 7<8 — สถิติลอยไม่มีแหล่ง + ชื่อ tool อาจแต่งขึ้น). บุ๋ยสั่งทิ้ง → ตั้ง status=archived + archived_reason (ไม่ลบไฟล์). ไม่ force publish/ไม่โกงด่าน editorial.

## 2026-07-01 — หยุด libra_kdp_sales_post (บุ๋ยสั่ง) ⛔
Codex สร้าง scripts/libra_kdp_sales_post.py (commit 7d754f5) โพสต์โปรโมตหนังสือ KDP แบบ organic ลงเพจ FB "AI ใช้จริง" (page 1167163833140098) ผ่านระบบ Loom + cron จ/พ/ศ 15:10 + daily 15:05 promo-window. **บุ๋ยสั่งหยุด** — เหตุผล: เอาโพสต์หนังสือสเปน (ADHD) ไปแปะเพจ AI ไทย = คนละกลุ่มเป้าหมาย ฟีดไม่โฟกัส. ลบ cron ทั้ง 2 ตัวแล้ว (สคริปต์ยังอยู่ ไม่ลบ). โพสต์เก่าที่ลงไป 1 โพสต์ (18:33 น. 1 ก.ค.) ยังค้างบนเพจ. **อย่าเปิด cron นี้กลับโดยไม่ถามบุ๋ย.**
