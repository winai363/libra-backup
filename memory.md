
## 2026-07-03 (บ่าย) — A+ ครบ 40/40 + ปกใหม่ขึ้น KDP + Free Promo อัตโนมัติ ✅

**A+ Content ครบ 40/40 เล่ม** — บั๊กที่แก้ระหว่างทาง (จำไว้ ทุกตัวมี pattern ใช้ซ้ำ):
1. `_is_signin` เช็คเฉพาะ URL path — query หลัง login มีคำ "signin" ทำ false-fail ที่ kdp.amazon.com (ec5d521)
2. Add Image modal: คลิกปุ่มยืนยันไล่จาก**ตัวท้าย**ที่ visible (ตัวแรก = ปุ่ม Add ของ module picker ที่ซ่อนอยู่) + fail-loud ถ้า modal ไม่ปิด (7202933)
3. **HTML entity หลุด** (`l&#x27;ansia`) ใน bullets → html.unescape บังคับ (3de3610)
4. **คำเคลมสุขภาพโดน A+ filter**: "These keywords violate our community guidelines: prevenire" → `_CLAIM_WORDS` regex กรอง prevent/cure/treat/heal/remedy/diagnose ทุกภาษา EU ออกจาก bullets (29171b9)
5. ASIN เคย applied จากรอบ fail → modal "Override existing content?" ต้องกด Override
- Diagnostics ถาวร: validation fail → กด Fix content + dump invalid elements อัตโนมัติ
- 5 เล่ม submitted ก่อนแก้ entity (dutch/fr/it) — ถ้า Amazon reject ตอน moderation ให้ regen assets + resubmit (ตัว batch ข้าม status=submitted ต้องลบ stamp ก่อน)

**Cover v2 ขึ้น KDP:** regen 40/40 (variant hash = slug+title กันเล่มชื่อคล้ายโคลนกัน; ใช้ actual_live_title 4 เล่มที่เคยถูก rename) → `reupload_covers.py` (โหมด --cover แตะแค่ไฟล์ปก ไม่โดน SEO/tag) ทยอยอัป 31 เล่มก่อน อีก 9 เล่มรอ A+ เสร็จแล้วค่อยตาม

**Free Promo อัตโนมัติ (บุ๋ยอนุมัติ):** `scripts/free_promo_auto.py` + cron 11:05 — เกณฑ์: LIVE≥7วัน + Enrolled + ไม่มียอด + ยังไม่เคยโปรในเทอม → ตั้งแจกฟรี 3 วัน (เริ่มพรุ่งนี้), 3 เล่ม/วัน, stamp listing.json free_promo, แจ้ง Telegram. เล่มแรกสำเร็จจริง: adhd-workbook-german-adults 4-6 ก.ค. "Success! Your promotion was added"
- UI gotchas หน้า promotion-manager: radio default = Countdown Deal ต้องติ๊ก Free Book Promotion ก่อน; ช่องวันที่**พิมพ์ไม่ได้** (jQuery datepicker + เดือน/ปีเป็น dropdown) ต้องคลิกวันใน `#ui-datepicker-div`; end-date disabled จน start ถูกเลือก; ปุ่ม Save = `span.a-button#promotion-manager-freebook-save-changes` + `input.a-button-input` ข้างใน (ไม่ใช่ <button>), enabled = ไม่มี class a-button-disabled

## 2026-07-03 — ปิด 3 งานที่บุ๋ยอนุมัติ: Select ครบ + subtitle ครบ + A+ Content full-auto (commit 4384e82) ✅

จาก audit เมื่อวาน บุ๋ยสั่ง "1. Go 2. Go 3. Go with full auto 4. (Ads) ยังก่อน":
1. **KDP Select 39/39 เล่ม LIVE** — `scripts/kdp_enroll_batch.py` (4 เล่มติดชื่อ listing ไม่ตรงชื่อจริงบน KDP → scrape ชื่อจริงมาใช้ + เก็บ `actual_live_title` ใน listing.json; ⚠️ 4 เล่มนี้ (adhd-adults-focus…, ai-side-hustles…, prompt-engineering-remote…, ai-powered-productivity…) ถ้า reupload metadata จะ RENAME เล่มจริง — ต้องเช็คก่อน)
2. **Subtitle ใหม่ 12 เล่ม** (title+subtitle <200) — reupload ครบทุกเล่ม LIVE รวม micro-wellness-remote-parents-de ที่เคยติด In Review
3. **A+ Content full-auto** — ระบบใหม่ 3 ไฟล์: `aplus_assets.py` (แบนเนอร์ 970x600: ปก+headline ภาษาถิ่น+bullets จาก description), `aplus_upload.py` (Playwright ครบ 6 stage: login ต่างตลาดผ่าน .env+TOTP → สร้าง content → อัปรูปด้วย composed DragEvents ทะลุ shadow DOM → ASIN typeahead (พิมพ์ช้าๆ ให้ dropdown เด้ง→คลิก suggestion→ติ๊ก checkbox→Apply content) → submit มี **modal ยืนยันซ้อน ต้องคลิก "Submit for approval" ตัวที่ 2**), `scripts/aplus_batch.py` (idempotent, ข้ามเล่มที่ aplus.status=submitted)
   - Gotchas: draft A+ ไม่ persist ถ้าไม่ Save/Submit (navigate ทิ้ง = หาย, ไม่มีขยะซ้ำ); cookies ต่างตลาดเก็บที่ `kdp_session_aplus.json` (gitignored, ห้ามเขียนทับ kdp_session.json หลัก); status หลัง submit = "Submitted → Awaiting approval" (Amazon รีวิว ≤7 วันทำการ)
   - **cron ใหม่ 10:40 ทุกวัน**: assets --all-live + batch → เล่มใหม่ที่ขึ้น LIVE ได้ A+ อัตโนมัติ
   - POC easy-taxes-self-employed-spain + sober-mocktails-de (ตลาด DE) submit สำเร็จ verify จาก screenshot แล้ว; batch ที่เหลือ 37 เล่มรัน background (log: logs/aplus_batch_20260703.log)
4. Amazon Ads = HOLD ตามคำสั่งบุ๋ย รอดูผล 1-3

**Cover redesign v2 (commit 262dac7+7aa277b) — งานที่บุ๋ยสั่งเพิ่ม เสร็จแล้ว:**
วิจัยตลาด (agent: Kindlepreneur 20k study, cookbook Top-50 data 84% พื้นสว่าง/46% hero เดียว, Greenleaf/Miblart 2026, ปกขายดีจริง ES/DE/FR) → เข้ารหัสลง cover_generator.py:
- calm: พาสเทลซีด→สีอิ่มเข้ม 6 variant (teal/terracotta/sage/indigo/plum/ochre) + title ขาวหนา + ป้าย WORKBOOK/CUADERNO/CAHIER อัตโนมัติ (จับคำใน title)
- authority (ภาษีสเปน): สีทึบสถาบัน + accent เหลือง + **ป้ายปี 2026** (ดึงปีออกจาก title มาเป็น badge) + strap หลายภาษา (_guess_lang จาก title)
- seniors: ขาว + ตัวดำใหญ่มาก + แถบแดง (สไตล์ Stiftung Warentest) + strap SCHRITT FÜR SCHRITT
- photo/food: กลับหัวเลย์เอาต์ — ชื่อใหญ่บนแผงครีม + รูป hero เดียวล่าง (prompt บังคับ 1 subject พื้นสว่าง)
- subtitle บนปกตัดเหลือ ≤95 ตัวอักษร (_short_sub), แม็กซ์ 2 บรรทัด
- **แก้บั๊ก detect_genre**: title หนัก 3×, tie-break หมวดเฉพาะชนะหมวดกว้าง, เพิ่มคำ wellness ภาษาถิ่น (anxiété/ansiedad/slaap/schlaf), เอา format words (workbook/journal) ออกจาก creative — เคยทำ TDAH→business, sleep→food, senior→tech
- ผล: ปกใหม่ใช้กับ**เล่มใหม่อัตโนมัติ**; ปกเก่า 39 เล่มยังไม่ regen **รอบุ๋ยสั่ง** (มี regen_covers.py+reupload_covers.py พร้อม); รายงาน+เทียบก่อน-หลัง: /root/downloads/cover-redesign/รายงานปกใหม่-3กค2026.html
- Gotcha ที่เจอ: kdp.amazon.com signin แบบ recognized-account (ไม่มีช่อง email) + URL หลัง login มีคำ signin ใน query → _is_signin เช็คเฉพาะ path (commit ec5d521)

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
