
## 2026-07-08 — Libra Distribution Dashboard + daily Telegram automation

- บุ๋ย approve ให้ทำตามแผน 100% automation เท่าที่ทำได้ และเตรียมส่วนที่ต้องทำผ่าน Claude for Chrome. เพิ่ม `distribution_report.py` เป็น source กลางของรายงาน distribution: แยกเงินจริง KDP royalties ออกจาก orders/free downloads ชัดเจน, อ่าน hero books, free promo windows, LovelyBooks flags, Reddit schedule, และสร้าง today actions.
- เพิ่มหน้าเว็บ/ไฟล์ให้ดูเอง: `/distribution` ใน Libra service, API `/api/distribution`, JSON `/root/libra/data/distribution-report.json`, HTML ดาวน์โหลดได้ที่ `/root/downloads/libra-distribution-dashboard.html`. หน้า HTML แสดงเงินจริง MTD, orders/downloads, free downloads เฉพาะ promo windows, KENP, LovelyBooks status, งานวันนี้ และกฎตัดสิน.
- เพิ่ม field กันสับสนใน `/api/dashboard/overview`: `real_mtd_royalties_usd`, `mtd_orders_all_types`, `mtd_kenp`, `money_warning`. ห้ามใช้ `revenue_30d_usd` เป็นเงินจริง เพราะยังเป็น estimate จาก units/free downloads; เงินจริงตอนนี้จาก state/log = `$2.06`, tracked orders/downloads = `32`, KENP = `25`.
- Automation: เพิ่ม `scripts/libra_distribution_report.py`; รันแล้วสร้าง report + Claude for Chrome guide ที่ `/root/downloads/kdp-pins/CLAUDE-CHROME-POSTING-GUIDE.md`; ส่ง Telegram ทดสอบสำเร็จ. ตั้ง cron system-level ทุกวัน `09:50` หลัง sales sync/promo report: `cd /root/libra && /usr/bin/python3 scripts/libra_distribution_report.py --send >> /root/libra/logs/distribution-report.log 2>&1`.
- Claude for Chrome handoff: guide สั่งให้โพสต์พินจาก `downloads/kdp-pins` เท่านั้น (ไม่ใช่ Etsy `pinterest-batch2`), วันละ 2-3 พิน, ใช้ caption จาก `CAPTIONS.txt`, ใส่ลิงก์ Amazon ให้ตรงเล่ม; LovelyBooks 25-26 ก.ค. ให้ตอบใน Leserunde และชวนโหลดฟรีจาก Amazon ห้ามส่งไฟล์เอง.
- Deploy/verify: `pytest tests/test_distribution_report.py tests/test_profit_tracker.py tests/test_feedback_loop.py tests/test_kdp_publish_confirmation.py -q` = 13 passed; `py_compile` ผ่าน; restart `libra.service` แล้ว active; curl `/distribution` HTTP 200 และ `/api/distribution` คืน `lovelybooks=ready`, `mtd_royalties_usd=2.06`, `mtd_kenp=25`. Telegram daily report ส่งจริงแล้ว 1 ครั้ง.
- ต่อรอบบุ๋ยบอกให้ล้างไฟล์ค้าง: ตรวจแล้ว `scripts/retry_set_price_adhd.sh` ไม่ใช่ขยะ แต่เป็น automation จำเป็นเพราะ `adhd-self-help-adults-es` ยังราคา `$5.99` และ cron 15:00 ต้อง retry จนกว่าจะลดเป็น `$2.99` โดยไม่ทำให้ royalty เหลือ 35%; commit แล้วพร้อม `bui_reminders.py` ที่แก้ path Pinterest เป็น `downloads/kdp-pins` และ handoff ล่าสุด. Verify `bash -n`, `py_compile`, targeted pytest ผ่าน; cron ยืนยันมี retry price / reminders / distribution report ครบ.

## 2026-07-08 — correction: อัปเดต LovelyBooks ใน strategy timeline + ระวัง dashboard revenue estimate

- บุ๋ยทักว่าข้อมูล LovelyBooks ในคำตอบยังเก่า ทั้งที่สำนักพิมพ์/ทีม LovelyBooks ตอบกลับมาแล้ว. ตรวจใหม่จาก actual source: `/root/lovelybooks/book_indexed.flag` = หน้านักเขียน WK Bui ขึ้นแล้ว, `/root/lovelybooks/leserunde_tool.flag` = `Aktion starten`, และ `/root/lovelybooks/shots/published.png` ถูกสร้าง 7 ก.ค. 16:12 แปลว่า Leserunde ohne Verlosung publish แล้ว.
- Root cause ของคำตอบเก่า: `/root/libra/data/strategy_timeline.json` ยังมี event stale ว่า "สมัคร LovelyBooks 12 ก.ค." และ `/root/memory.md` ยังบอก "รอทีมตอบ 13-15 ก.ค."; dashboard `/api/strategy` จึงพาให้สรุปผิดถ้าไม่เปิด lovelybooks flags/logs. แก้แล้ว: timeline เป็น done วันที่ 7 ก.ค. และ action ของบุ๋ยเปลี่ยนเป็นตอบคอมเมนต์/ชวนโหลดฟรี 25-26 ก.ค.; root memory อัปเดตว่าไม่รอตอบแล้ว.
- อ่าน Libra ใหม่ทั้งระบบ: `/api/strategy` summary = 41 live ebooks, 4 paperbacks submitted/live, MTD royalties $2.06, lifetime $4.12; roster actual ล่าสุด `/root/kdp/bookshelf-roster.json` fetched 14:07 = LIVE 45, orphan 0, duplicate 0. LovelyBooks ไม่ใช่งานค้างสมัครแล้ว.
- ระวังตัวเลข dashboard/profit: `/api/profit/portfolio` ยังแสดง estimated revenue 30d $71.12 เพราะคำนวณจาก units/free downloads บางส่วนเหมือน paid; source of truth เงินจริงต้องยึด `kdp_sales_sync` log `royalties=2.0638560641 USD` วันที่ 8 ก.ค. อย่ารายงาน $71.12 เป็นเงินจริง.

## 2026-07-08 — verify state + archive 3 junk drafts + commit FX fix (commit e3af667)

**บุ๋ยสั่ง "ลุยตามคำแนะนำ แต่เช็คให้ชัวร์ก่อน" → verify แล้วลงมือ:**
- **ตัวเลขยอดขายอ่านให้ถูก:** `kdp_sales_sync` log จะพิมพ์ `digitalOrders=115` แต่นั่นคือตัวนับ overview widget (คละแจกฟรี/ทุกตลาด) **ไม่ใช่เงิน**. เงินจริง = `totalRoyalties` = **$2.06 MTD ก.ค.**. per-title จริงมีแค่ 3 เล่ม (17+13+2 orders). อย่ารายงาน 115 เป็นยอดขาย.
- **Archive 3 DRAFT strays** ด้วย `kdp_unpublish.py` (อัปเดต TARGETS ให้ตรง roster ปัจจุบัน, action=archive ทุกตัว เพราะ draft ไม่มีปุ่ม Unpublish): Advanced Python orphan (B0H365SW7S) + italian dup (B0H3FJNK8Z) + mocktails dup (B0H38QFT5Z). ตัวจริง LIVE เก็บครบ. verify: roster ใหม่ DRAFT=0 orphan=0 dup=0, LIVE ยัง 41.
- **Commit FX fix ที่ค้าง** ใน `kdp_sales_sync.py` (session ก่อน 7ก.ค. เพิ่ม `FX_TO_USD`/`_to_usd` แต่ไม่ได้ commit) — กันรายได้ non-USD ถูกบันทึกเป็น $0 ใน winner_signals/profit_tracker.
- **🐛 แก้บั๊ก roster false-alarm (commit 03e2501):** "4 เล่ม UNPUBLISHED" **เป็นข้อมูลผิด** — badge จริงบน KDP คือ "Live With unpublished changes" (เล่มยัง LIVE ขายอยู่ แค่มี draft edit ค้างไม่ submit). `classify_status` เช็ค `"unpublish" in t` ก่อน `"live"` เลยตีผิด. แก้ให้เช็ค "live" ก่อน → roster ตอนนี้ LIVE 45, UNPUBLISHED 0, backfill live_status กลับถูก. **บทเรียน: อย่าเชื่อ label ที่ derive มา ตรวจ badge ดิบก่อน.**
- **⚠️ ค้าง (low prio):** 4 เล่มนั้นมี "unpublished changes" (Continue setup) = edit ค้างไม่ submit บน KDP — live version ขายปกติ ไม่กระทบรายได้ แต่ควรเคลียร์ (submit หรือ discard). น่าจะตกค้างจาก cover-v2 reupload 3ก.ค. ที่ save draft แต่ไม่ publish.
- **(ข) toolkit paid vs free:** digitalOrders ใน dashboard **รวมยอดแจกฟรี** (126 orders แต่ royalty รวม $2.28 = $0.018/order เป็นไปไม่ได้ถ้า paid หมด). toolkit $2.99@70%=~$1.98/เล่ม, royalty=$1.98 → **ขายจริง ~1 เล่ม**, ที่เหลือ ~16 = แจกฟรี. เดือนนี้ทั้งพอร์ตขายจริง ≈ 1 เล่ม + KENP 71 หน้า. endpoint promotions/freePromo ไม่มี (คืน HTML).
- **✅ security:** เพิ่ม `data/ebrolis_session.json` + `data/.lovely-profile/` เข้า .gitignore แล้ว (commit 8d2929d).

## 2026-07-08 (บ่าย) — เคลียร์ "unpublished changes" 4 เล่ม + full context review + แผน distribution

**เคลียร์ draft edit ค้าง (ผลจริง 1/4 — อย่ารายงานเกิน):** ใช้ `kdp_finish_publish.py <slug> [price]` (ไปหน้า pricing กด Publish ส่ง draft ค้าง).
- ✅ `deducciones-fiscales-autonomos-espana-2026` — republish สำเร็จ ขึ้น "Updates in review" (เล่มนี้เคยติด Passkey error ตอน submit)
- ❌ อีก 3 เล่ม (sleep-dutch, ai-productivity-homeoffice-de, cozy-romantasy) — ติด validation ในหน้า pricing ("Please complete this" 2 จุด = KDP Select/territory rights/ราคา primary market ไม่ stick ผ่าน automation React form). เล่มยัง LIVE ขายปกติ = cosmetic → **หยุด ไม่ไล่แก้บนเล่มที่ขายอยู่** (ค่า cosmetic ต่ำ เสี่ยงสูง + เดือนนี้ตั้งใจแตะน้อย)
- **🐛 แก้ 2 บั๊กใน kdp_finish_publish (commit ล่าสุด):** (1) hardcode ราคา $2.99 → เพิ่ม arg `[price]` (homeoffice-de=$4.99 เกือบโดนลดราคา!); (2) เช็ค error ด้วย exact text `"Please fix the highlighted error"` แต่จริงเป็น `"...error(s) to continue"` → match ไม่เจอ = **รายงาน SUCCESS ปลอม**. แก้เป็น substring. **บทเรียน: tool บอก success ต้องดูภาพจริงยืนยัน (เคยเชื่อ log แล้วผิด).**
- **ต้นตอ 3 เล่มที่เคลียร์ไม่ได้ = KDP "preorder was cancelled" state** (diagnostic 5 รอบ) → error 2 จุดไม่ผูกฟิลด์มาตรฐาน (ราคา/royalty/territory ครบหมด). ทำ auto ไม่คุ้ม/เสี่ยงบนเล่มที่ขายอยู่ → **หยุด รายงานบุ๋ย** (แก้มือ 2 นาที/เล่ม ถ้าอยากเรียบ). อย่าเสียเวลาไล่แก้อีกถ้าไม่มีคำสั่ง.

**8ก.ค. — 30-Day Turnaround (จาก Antigravity) เริ่ม auto: review-link + free-book posts:**
- **Verdict แผน 4 ข้อ:** #1 สุมไฟฟรี ✅ทำ · #2 ลิงก์รีวิวท้ายเล่ม ✅ROI สูงสุด (แก้ URL→per-marketplace) · #3 KENP page-flip ⚠️ข้าม (รายได้จิ๋ว+Amazon จับ manipulation+ต้องเล่มใหม่) · #4 A+ ไส้ใน ✅รอ A+ รอบแรกเคลียร์ ~14ก.ค.
- **#2 สร้าง `scripts/add_review_link.py`** — inject ลิงก์ create-review **per-marketplace + localized CTA** (EN→.com, ES→.es, DE→.de ฯลฯ; ลิงก์ .com ตายสำหรับ 88% แคตตาล็อก) ลงหลัง heading รีวิวใน ebook.md + rebuild EPUB. **ทำ+verify แล้ว 10 เล่ม** (ฮีโร่ ES/DE + อังกฤษ) local — ลิงก์ฝังใน EPUB จริง. ⚠️ **ASIN รู้หลัง publish → tool นี้ต้องรัน post-publish** (template แก้ไม่ได้เพราะ write time ยังไม่มี ASIN).
- **🔴 Re-upload ติด blocker:** `kdp_upload.py <slug> --update` (arg: slug ก่อน flag) → `require_quality_gate` เช็ค **paperback PDF content (หน้า/ตัวอักษร) เสมอ** แม้อัปแค่ ebook; easy-taxes มี paperback.pdf พัง (1หน้า/0char) เลยบล็อก. **ยังไม่ re-upload เล่มไหนขึ้น KDP** (live ไม่โดนแตะ). Proper fix (follow-up): ให้ `validate_book` ข้าม paperback CONTENT checks เมื่อ require_pdf=False หรือ regen paperback PDFs. **อย่า hack gate รีบๆ.** revert การแก้ gate ที่ไม่สำเร็จแล้ว.
- **#1 `/root/downloads/kdp-freebook-posts.md`** — โพสต์ r/FreeEBOOKS+FB สำหรับเล่มอังกฤษที่ฟรีอยู่: freelance-designers (ถึง10ก.ค.), workflows-professionals (ถึง9ก.ค.), anxiety-men-40 (9-11). **งานบุ๋ยโพสต์เอง** (server IP โดนบล็อก).

**8ก.ค. — Pinterest workflow: บุ๋ยเลือกใช้ Claude for Chrome (client-side) โพสต์พิน KDP:**
- กฎ "Pinterest อัพมือเท่านั้น" = ห้าม **server automation** (session ใหม่+IP บอท = แบน). แต่ **Claude for Chrome (รันในเบราว์เซอร์บุ๋ยที่ login จริง) OK** — เสี่ยงต่ำ. ผม (server) สั่ง Chrome บุ๋ยไม่ได้ = เตรียม prompt ให้บุ๋ยวางเอง.
- ⚠️ พินหนังสือ KDP จริงอยู่ `/root/downloads/kdp-pins/` (15 พิน 5 เล่ม + CAPTIONS.txt) — **ไม่ใช่** `pinterest-batch2` (นั่น Etsy!). เตรียม `kdp-pins/PIN-QUEUE-claude-chrome.md` (2 รอบ: workbook วันนี้ / focus พรุ่งนี้). ลิงก์: workbook=B0H6VB1SDX, focus=B0H6V4RNJ2.
- ทางถาวร auto จริง = Pinterest API (ยังติด "Trial access pending" — เช็คว่าปลดล็อกยังถ้าบุ๋ยอยากให้ผมทำเองทั้งหมด).

**8ก.ค. (เย็น) — Option 1: ตั้งวันแจกฟรีเล่มฮีโร่คู่ตัวขยายฟรี (บุ๋ยเลือก):**
- แผน free-only เดิม = "holding pattern": เล่มฮีโร่ 4 เล่ม status `Planned-HOLD` (รอ paid stack ที่เลื่อนไป checkpoint) → ก.ค. ไม่มีข้อมูลเล่มฮีโร่. บุ๋ยเลือกจับคู่กับตัวขยาย**ฟรี**ที่มี.
- **เพิ่ม `--start/--days/--force` ใน `free_promo_auto.py`** (เดิมตั้งได้แค่ "พรุ่งนี้+3วัน"); datepicker รองรับวันอนาคต/ข้ามเดือนอยู่แล้ว. `--force`+`--only` ข้าม auto-filter (has_sales/min-age) สำหรับ manual scheduling เจตนา (safety check ในฟอร์มยังทำงาน). commit แล้ว.
- **ตั้งจริง verify แล้ว (ไม่ผูกเงิน):** workbook-es (adhd-adults-workbook-es) 15-19ก.ค. + focus-es (adhd-adults-focus-work-relationships-es) 22-26ก.ค. คู่ Pinterest; german (adhd-workbook-german-adults, เหลือ 2 วัน) 25-26ก.ค. คู่ LovelyBooks Leserunde. เก็บ promo เก่า german ไว้ใน `free_promo_history`.
- **taxes (easy-taxes) ไม่ตั้ง** — ไม่มีตัวขยายฟรี (ไม่ใช่ ADHD/ไม่มี LovelyBooks) + มียอดขายแล้ว; รอบุ๋ยทำ tax Pinterest pins หรือรอ checkpoint.
- ⚠️ **บุ๋ยต้องอัพ Pinterest batch2 ให้พินชี้ workbook-es โผล่ ~15-19ก.ค. และ focus-es ~22-26ก.ค.** (นี่คือตัวขยาย ถ้าไม่มีพิน = ฟรีเดี่ยว 2-7 โหลด). LovelyBooks 25-26 auto (watcher เตือนแล้ว).

**บุ๋ยสั่ง "ทำทุกข้อ" (ก/ข/ค/ง) — ผลจริง:**
- **(ก) เครื่องวัด:** ✅ Telegram watcher LovelyBooks (`/root/lovelybooks/check_book_indexed.py`+`check_leserunde_tool.py`) เดิมชี้ loom/.env (token 401 ตาย) → ชี้ `/root/libra/.env` (bot=Bui_libra_bot ใช้ได้) + var `TELEGRAM_CHAT_ID`; test ping เข้าแล้ว. royalty 35% ADHD lead ยังล็อค (2วันหลัง promo) — gate+cron retry จัดการ, flag ไว้. A+ 40 submitted 3ก.ค. เช็คจริง ~14ก.ค.
- **(ค) artifact แผน:** Artifact tool ถูกปิด (don't-ask mode) → เซฟ HTML ที่ `/root/downloads/libra-distribution-plan.html` (เปิดผ่าน FileBrowser). ครบ: สถานะจริง/รากปัญหา 3ชั้น+โครงสร้าง/แผน A+B/คำถามพอร์ต/กฎห้ามข้าม.
- **(ง) คำถามพอร์ต:** digital product ทั้งหมด (BuiBook ฿0/Etsy 0/KDP ~$4) ตัน distribution เหมือนกัน; ตัวทำเงินจริง=Shopee VDO เกาะไวรัล FB (สินค้า+ผู้ชมที่เดียวกัน). เสนอบุ๋ยคิด: ลงทุน distribution ให้ KDP (ES/DE) หรือถือ passive แล้วโยกโฟกัส — ตัดสินที่ checkpoint. **ยังไม่ทำเอง.**

## 2026-07-05 — รับช่วงต่อ lock ค้าง: feedback loop + cover title depth + paperback prep

- บุ๋ยให้ Codex ปลด lock เก่าของ Claude แล้วรับงาน `/root/libra` ต่อเอง; abort เฉพาะ coordination lock ไม่ล้าง diff.
- แก้ `feedback_loop.py` ให้ metric จาก KDP ที่เป็น `n/a`/blank/`None` ไม่ทำให้ analyze/record snapshot crash; `feedback_loop.py --all` ผ่านกับข้อมูลจริง.
- แก้ `cover_generator._fit()` กัน title ล้นลึกเกิน max_lines: เมื่อฟอนต์ถึงขั้นต่ำแล้วยังยาว จะ truncate บรรทัดสุดท้ายด้วย ellipsis แทนปล่อย block ชนส่วนอื่น.
- เพิ่ม CLI usage guard ใน `scripts/kdp_paperback_upload.py` เพื่อไม่ crash เมื่อรันผิดแบบไม่ใส่ slug.
- เพิ่ม test เฉพาะ `tests/test_feedback_loop.py` และ `tests/test_cover_generator.py`; verify `py_compile` ผ่าน, pytest ชุดสำคัญ 23 passed.

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
