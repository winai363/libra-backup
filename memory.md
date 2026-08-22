
## 2026-07-10 — Distribution Monitor Dashboard

- บุ๋ยถามสถานะแผน Libra และขอ dashboard monitor; ตรวจ state จริงก่อนสร้าง:
  - แผน active = `Depth Loop — 5 เล่มฮีโร่ · เดือนนี้ทดลองช่องทางฟรีล้วน`, checkpoint `2026-07-31`
  - เงินจริง MTD จาก `/root/kdp/sales-sync-state.json` = `$6.77`; orders/downloads = `60`; KENP = `159`
  - Hero books: LIVE 5/5, KDP Select 5/5, A+ submitted 5/5
  - Pinterest manual progress = 2/4 done; เหลือ `adhd-workbook-german-adults`, `easy-taxes-self-employed-spain`
  - Category health ok, blocker 0, warning 26; KDP queue 0
- เพิ่ม monitor ใหม่:
  - API: `/api/distribution/monitor` (ผ่าน nginx คือ `/libra/api/distribution/monitor`)
  - HTML: `/distribution/monitor` (ผ่าน nginx คือ `/libra/distribution/monitor`)
- แก้ `distribution_report.py`:
  - `build_monitor(report, overview=None, category_health=None)` คำนวณ score/status/blockers/health/recommendation
  - `render_monitor_html(monitor)` แสดงหน้าอ่านครั้งเดียว: plan score, เงินจริง, orders/downloads, KENP, checkpoint, hero setup, Pinterest, blockers, system health, promo calendar
  - Blocker logic ตั้งใจนับเฉพาะสิ่งที่หยุดแผน hero distribution จริง; old `quality_failed` drafts ไม่ใช่ blocker ของแผนเดือน ก.ค.
- แก้ `app.py` เพิ่ม route monitor ทั้ง JSON และ HTML
- เพิ่ม tests ใน `tests/test_distribution_report.py`; RED fail ก่อนแก้เพราะยังไม่มี functions, GREEN ผ่านหลังแก้
- Verify:
  - `PYTHONPATH=. pytest tests/test_distribution_report.py tests/test_profit_tracker.py tests/test_feedback_loop.py tests/test_kdp_publish_confirmation.py -q` = 16 passed
  - `python3 -m py_compile app.py distribution_report.py tests/test_distribution_report.py` ผ่าน
  - restart `libra.service` แล้ว active
  - public `/libra/api/distribution/monitor` คืน `status=on_track`, `score=92`, `blockers.count=0`
  - public `/libra/distribution/monitor` มี `Libra Monitor`, `On track`, `$6.77`, `2/4 done`, `รอ checkpoint ก่อนซื้อ paid promo`

## 2026-07-10 — Actual vs Plan + KDP Auto Manager Agent

- บุ๋ยขอ Actual vs Plan แบบ bar chart และให้วาง role CFO/COO/CMO/KDP Strategist เพื่อคุม Libra KDP เป็นระบบ auto
- เพิ่ม target รอบเรียนรู้ถึง checkpoint `2026-07-31` ใน `distribution_report.py`:
  - Revenue royalties `$25.00`
  - Orders/downloads `120`
  - KENP `500`
  - Free downloads `100`
  - Pinterest `4/4`
  - Blockers `0`
- เพิ่ม `monitor["actual_vs_plan"]`:
  - `metrics` สำหรับ bar chart: Revenue, Orders/downloads, KENP, Free downloads, Hero LIVE, KDP Select, A+ submitted, Pinterest, Blockers
  - `roles` สำหรับ CFO/COO/CMO/KDP Strategist พร้อม status/verdict/target
- เพิ่ม `monitor["kdp_agent"]`:
  - name = `Libra KDP Auto Manager`
  - mode = `auto_advisor`
  - authority = read/diagnose/set targets/recommend only; no paid spend or KDP mutation without guard/approval
  - next actions ล่าสุด: อย่าเพิ่งซื้อ paid promo, เร่ง Pinterest ที่เหลือ, หลังแต่ละ free promo ให้เทียบ downloads/KENP/reviews/royalties กับ target
- ปรับ `/distribution/monitor` ให้แสดง:
  - Actual vs Plan bar chart
  - KDP Auto Manager Agent
  - CFO / COO / CMO / KDP Strategist role cards
- เพิ่ม endpoint `/api/kdp-agent` (public path `/libra/api/kdp-agent`) เพื่อดู agent state แยก
- เพิ่ม `scripts/kdp_auto_manager.py` เขียน `data/kdp-agent-state.json` แบบ read-only
- ตั้ง cron system-level ทุกวัน 10:05 หลัง distribution report:
  - `cd /root/libra && /usr/bin/python3 scripts/kdp_auto_manager.py >> /root/libra/logs/kdp-agent.log 2>&1`
- เพิ่ม `data/kdp-agent-state.json` เข้า `.gitignore` เพื่อไม่ให้ runtime state ทำ repo dirty ทุกวัน
- Verify:
  - RED tests fail ก่อนแก้เพราะไม่มี Actual vs Plan/agent/bar chart
  - `PYTHONPATH=. pytest tests/test_distribution_report.py tests/test_profit_tracker.py tests/test_feedback_loop.py tests/test_kdp_publish_confirmation.py -q` = 16 passed
  - `python3 -m py_compile app.py distribution_report.py tests/test_distribution_report.py scripts/kdp_auto_manager.py` ผ่าน
  - `python3 scripts/kdp_auto_manager.py` เขียน state สำเร็จ: `status=on_track score=92 blockers=0`
  - restart `libra.service` แล้ว active
  - public monitor มี `Actual vs Plan`, `bar-fill`, `$25.00`, CFO/COO/CMO/KDP Strategist และ `อย่าเพิ่งซื้อ paid promo`
  - public `/libra/api/kdp-agent` คืน JSON agent state

## 2026-07-10 — KDP Auto Manager daily operating loop

- บุ๋ยสั่ง “ต่อ” หลังเพิ่ม Actual vs Plan และ KDP Auto Manager
- เพิ่ม `action_queue` และ `decision_gates` ให้ `monitor["kdp_agent"]`:
  - CMO: `เร่ง Pinterest ที่เหลือให้ครบ 4/4`, due `ก่อน 2026-07-15`, status `due_now`
  - KDP Strategist: สรุปผลหลัง free promo windows, status `scheduled`
  - Paid promo gate = `closed` จนกว่าจะมี proof หลัง promo windows หรือถึง checkpoint
  - Amazon Ads gate = `closed` จนกว่า royalties/KENP/review signal ชี้ buyer intent จริง
  - Scale content gate = `open` สำหรับงานฟรี/manual ที่ไม่มี spend risk
- เพิ่ม `kdp_agent_digest(state)` ใน `distribution_report.py` เพื่อสร้างข้อความ Telegram digest จาก agent state
- ปรับ `scripts/kdp_auto_manager.py`:
  - เพิ่ม `--send`
  - เมื่อใส่ `--send` จะส่ง Telegram digest ด้วย `send_telegram()`
  - ยังเป็น read-only: ไม่ซื้อ paid promo, ไม่เปลี่ยนราคา, ไม่ publish, ไม่ mutate KDP
- อัปเดต cron system-level เป็น:
  - `5 10 * * * cd /root/libra && /usr/bin/python3 scripts/kdp_auto_manager.py --send >> /root/libra/logs/kdp-agent.log 2>&1`
- ปรับหน้า `/distribution/monitor` ให้แสดง `Action Queue` และ `Decision Gates`
- Verify:
  - RED tests fail ก่อนแก้เพราะไม่มี queue/gates/digest
  - `PYTHONPATH=. pytest tests/test_distribution_report.py tests/test_profit_tracker.py tests/test_feedback_loop.py tests/test_kdp_publish_confirmation.py -q` = 17 passed
  - `python3 -m py_compile app.py distribution_report.py tests/test_distribution_report.py scripts/kdp_auto_manager.py` ผ่าน
  - `python3 scripts/kdp_auto_manager.py --send` ได้ `sent=True`
  - restart `libra.service` แล้ว active
  - public monitor มี `Action Queue`, `Decision Gates`, `Paid promo gate`, `Amazon Ads gate`
  - public `/libra/api/kdp-agent` คืน `CMO due_now Paid promo gate closed`

## 2026-07-10 — Auto Free Growth Engine

- บุ๋ยขอให้ agent ตัดสินใจปล่อยฟรีหรือโพสต์ฟรีได้ก่อนสิ้นเดือนโดยใช้ข้อมูลจริง เพื่อ maximize revenue ใน timeline จำกัด
- ตรวจ official KDP rules แล้ว:
  - Free Book Promotion ใช้ได้สูงสุด 5 วันต่อ KDP Select 90-day term
  - ต้อง schedule ล่วงหน้าอย่างน้อย 1 วัน
  - ระหว่าง free promotion ไม่มี royalty
  - ในหนึ่ง term ใช้ Free Promo หรือ Countdown ได้อย่างใดอย่างหนึ่ง
- เพิ่ม `build_free_growth_engine(report, actual_vs_plan, blockers)` ใน `distribution_report.py`
  - blockers มี → hold
  - มี promo active/ใกล้เริ่มใน 5 วัน หรือ Pinterest/manual distribution ยังไม่ครบ → decision `free_post`
  - ไม่มี promo ใกล้เริ่ม + revenue/free-download progress behind + มีวันถึง checkpoint + มี hero LIVE/Select ไม่มี free_promo → decision `free_promo`
- เพิ่ม `free_growth_engine` เข้า `kdp_agent`
- ปรับ monitor HTML ให้แสดง `Free Growth Engine` ตาราง action/channel/reason/auto execute
- ปรับ digest ให้รวม Free Growth Engine decisions
- ปรับ `scripts/kdp_auto_manager.py`:
  - เพิ่ม `--execute-free-actions`
  - decision `free_post` จะบันทึก action log และส่งผ่าน digest/Telegram/reminder path
  - decision `free_promo` จะเรียก `scripts/free_promo_auto.py --force --only <slug> --start <tomorrow> --days <n>` เมื่อ guard เปิด
  - เขียน action log ที่ `data/kdp-agent-actions.jsonl`
- เพิ่ม `data/kdp-agent-actions.jsonl` เข้า `.gitignore`
- อัปเดต cron เป็น:
  - `5 10 * * * cd /root/libra && /usr/bin/python3 scripts/kdp_auto_manager.py --send --execute-free-actions >> /root/libra/logs/kdp-agent.log 2>&1`
- Verify:
  - RED tests fail ก่อนแก้เพราะไม่มี `free_growth_engine` และ `build_free_growth_engine`
  - `PYTHONPATH=. pytest tests/test_distribution_report.py tests/test_profit_tracker.py tests/test_feedback_loop.py tests/test_kdp_publish_confirmation.py -q` = 18 passed
  - `python3 -m py_compile app.py distribution_report.py tests/test_distribution_report.py scripts/kdp_auto_manager.py` ผ่าน
  - `python3 scripts/kdp_auto_manager.py --execute-free-actions` executed=1, action=`free_post`
  - `python3 scripts/kdp_auto_manager.py --send --execute-free-actions` sent=True, executed=1
  - restart `libra.service` active
  - public `/libra/api/kdp-agent` คืน `free_post True Pinterest/Reddit`
  - public `/libra/distribution/monitor` มี `Free Growth Engine`, `free_post`, `Auto execute`
- สถานะ decision ล่าสุด: ยังไม่ schedule free promo ใหม่ เพราะมี promo ใกล้เริ่ม 15 ก.ค. และ Pinterest/manual distribution ยังไม่ครบ; agent เลือก auto free_post เป็น action ที่เหมาะสุดตอนนี้

## 2026-07-10 — Pinterest completed 4/4 after Claude for Chrome posting

- บุ๋ยแจ้งว่าโพสต์ Pinterest เสร็จแล้วจาก Claude for Chrome
- อัปเดต `data/manual-task-state.json`:
  - completed เพิ่ม `adhd-workbook-german-adults`
  - completed เพิ่ม `easy-taxes-self-employed-spain`
  - last_updated = `2026-07-10`
  - note ระบุว่าเป็น user report จากการโพสต์ผ่าน Claude for Chrome
- Regenerate report/agent:
  - `python3 scripts/libra_distribution_report.py`
  - `python3 scripts/kdp_auto_manager.py`
- Verify:
  - `PYTHONPATH=. pytest tests/test_distribution_report.py tests/test_profit_tracker.py tests/test_feedback_loop.py tests/test_kdp_publish_confirmation.py -q` = 18 passed
  - `python3 -m py_compile distribution_report.py scripts/kdp_auto_manager.py scripts/libra_distribution_report.py` ผ่าน
  - restart `libra.service` แล้ว active
  - public `/libra/api/distribution/monitor` คืน score `100`, Pinterest `4/4 done`, CMO `on_plan`, blockers `0`
  - public `/libra/api/kdp-agent` คืน CMO `on_plan`, free_growth action `free_post`

## 2026-07-10 — Auto AI growth loop activated

- บุ๋ยอนุมัติให้เริ่มระบบ auto AI เพื่อเร่งยอด
- ตรวจ cron จริง:
  - 09:50 distribution report sends Telegram
  - 10:05 KDP Auto Manager runs `--send --execute-free-actions`
  - 11:05 free promo auto runs
  - 20:00 Reddit promo reminder runs
- Manual trigger ทันที:
  - `python3 scripts/kdp_auto_manager.py --send --execute-free-actions`
  - output: `kdp_auto_manager status=on_track score=100 blockers=0 send=True sent=True execute_free=True executed=1`
- Current free growth decision = `free_post True Pinterest/Reddit`
- No paid spend, no Amazon Ads, no unsafe KDP mutation. Free promo scheduling remains guarded and will only call `free_promo_auto.py` if data says no near promo + progress behind + eligible hero remains.
- Public `/libra/api/distribution/monitor` ล่าสุด: score `100`, Pinterest `4/4 done`, blockers `0`, free_growth `free_post True`
- Action log wrote latest runtime row to `data/kdp-agent-actions.jsonl`



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
# 2026-07-11 — 90-Day Profit Agent activated

- Activated verified SQLite business ledger, immutable KDP snapshots, cost inventory, Profit A/B, title-specific experiments, persisted no-spend policy, audited actions/retries/manual completion, and 30/60/90 checkpoints.
- Production baseline: KDP royalties `$7.63`, 256 all-type orders/downloads, 361 KENP; attributed top titles `$6.85`, disclosed gap `$0.78`; verified known direct costs `$7.40`; preliminary Profit A `$0.23`, but cost inventory is incomplete so positive contribution is not yet proven.
- Three zero-cost experiments: `adhd-self-help-adults-es` metadata, `ai-augmented-productivity-toolkit` free promo, `acuarela-para-principiantes-guia-paso-a-paso` category. All are `manual_required` pending external before/after evidence; no KDP mutation was claimed or performed by the new controller.
- Daily profit agent cron is 10:15. Conflicting KDP writers paused for the 90-day mode: upload queue, A+, free-promo auto, ADHD price retry, and legacy KDP auto manager; new-title generation remains paused.
- Verification: final review approved; main full suite `142 passed`; production dry-run read-only regression covered; `libra.service` active; profit API/page HTTP 200; session files 600.
- Follow-up 11:48: scheduled remaining 2 KDP Select free-promo days for `ai-augmented-productivity-toolkit` on 2026-07-12..13. Audited action attempt 2 as executed; experiment 2 now cooldown until 2026-07-25. ADHD title-change and watercolor invalid-category actions remain manual_required and must be redesigned, not blindly submitted.
- Handoff bug/data cleanup: `book-one` and `free-book` missing cost_inventory rows are test contamination caused by first two `tests/test_profit_tracker.py` tests not monkeypatching `LEDGER_FILE`; fix tests then delete rows. Three real legacy uploaded titles still lack cost reports: `ai-productivity-workflows-professionals`, `anxiety-workbook-young-women-de`, `cozy-historical-romantasy-german`.

# 2026-07-11 (บ่าย) — Claude รับช่วง: เคลียร์งานค้าง handoff ครบ 4 ข้อ

**1. Test contamination fixed:** เทสต์ 2 ตัวแรกใน `tests/test_profit_tracker.py` patch แค่ `KDP_DIR` ไม่ patch `LEDGER_FILE` → รันเทสต์ทีไรเขียน `book-one`/`free-book` ลง production ledger. เพิ่ม monkeypatch แล้ว + ลบ 2 แถวปลอมออกจาก `cost_inventory` แล้วรันเทสต์ซ้ำยืนยันไม่งอกกลับ. ตารางอื่น (direct_costs, cost_report_versions, snapshots) ตรวจแล้วไม่มี contamination.

**2. ระบบ cost estimate (สถานะใหม่ `estimated`):** 3 เล่ม legacy (`ai-productivity-workflows-professionals`, `anxiety-workbook-young-women-de`, `cozy-historical-romantasy-german`) ไม่มี cost-report.json จริงและกู้ไม่ได้ (สร้างก่อนมีระบบ log ต้นทุน) →
- สร้าง `cost-estimate.json` ในโฟลเดอร์เล่ม ($0.50/เล่ม = ceiling เหนือ max จริง $0.4523 จาก 51 เล่มที่มี log, mean $0.1752)
- `ingest_uploaded_title_costs` รองรับ fallback: cost-report=verified, cost-estimate=`estimated` (นับเป็นต้นทุนใน contribution แต่**ไม่มีวัน**เป็น verified / ไม่ทำให้ cost_complete หรือ positive_contribution_proven เป็นจริง)
- กัน double-count: ถ้าเล่มได้ cost-report จริงทีหลัง estimate จะถูก supersede อัตโนมัติ (ทั้ง ingest status และ portfolio_financials)
- แก้ `title_contribution` + `profit_agent.title_financial_boundary` ให้ estimated ≠ cost_complete
- **ตัวเลขจริงหลัง ingest:** 42 เล่ม = 39 verified + 3 estimated + 0 missing; costs $7.40→$8.90; contribution +$0.23→**−$1.27** (ตัวเลขซื่อสัตย์ขึ้น — เดิม underreport เพราะ 3 เล่มนับต้นทุน $0)

**3. ปิด 2 experiments ที่ไม่ปลอดภัย (audit trail ครบ):**
- Exp 1 (adhd-self-help-adults-es เปลี่ยน title) → `inconclusive` ไม่เปิดใหม่: title ใหม่ไม่ตรงปก/เนื้อหา + เป็นเล่มทำเงินอันดับ 1 ห้ามเสี่ยงช่วง 90 วัน
- Exp 3 (acuarela หมวด "Watercolor Painting") → `inconclusive`: หมวดไม่มีจริงใน KDP tree (audit 27มิ.ย. 2,419 leaves)
- **Exp 4 ใหม่ (cycle 2 acuarela):** category_update → `Crafts, Hobbies & Home > Crafts & Hobbies > Painting` (leaf จริง, แทนหมวดที่ fit น้อยสุด "Instruction & Reference > Color") — daily run แรกเดินเป็น `ready` แล้ว รอ executor/manual execution พร้อม before/after evidence
- ทั้งสองปิดผ่าน UPDATE + `record_action_result` internal_transition (ตาม pattern run_daily) + เพิ่ม comment เตือนที่ `APPROVED_EXPERIMENTS` กันคนหยิบค่า unsafe ไป seed ใหม่

**4. Verify:** full suite **144 passed** (142 เดิม + 2 เทสต์ใหม่เรื่อง estimate), daily agent live run ปกติ, restart libra.service แล้ว /profit ตอบ 200. Gate `cost_completeness` ตอนนี้ **closed** (เพราะ 3 เล่ม estimated) — ถูกต้องตามหลัก อย่าพยายาม "แก้" gate นี้ด้วยการ mark verified; ทางเดียวที่เปิดได้คือมี cost report จริง.

**เหลือให้มนุษย์/รอบหน้า:** Exp 4 category change ต้อง execute บน KDP UI + บันทึก evidence ผ่าน `scripts/libra_profit_agent_daily.py --complete-action <key> --evidence-json ...`; Free promo 12-13 ก.ค. รอวัดผล; Exp 2 evaluate หลัง 25 ก.ค.

# 2026-07-11 (เย็น) — Auto-Executor: ปิดลูป 90-Day Profit Agent เป็น full-auto

**บุ๋ยสั่ง: "ให้ทำเป็น auto ทั้งหมด แต่ทำด้วยความระมัดระวังและตรวจเชค"**

**สร้าง `scripts/kdp_action_executor.py`** — executor สำหรับ run_daily(executor=...) ทำให้ experiment actions execute เองบน KDP:
- **Safety gates (pure function, มีเทสต์):** category ต้องมีจริงใน tree ที่ audit แล้ว, replaces ต้องอยู่บน listing, title change = ปฏิเสธถาวร, paid = ปฏิเสธ, kind ที่ไม่รองรับ → คง manual_required
- **Real-state verification:** อ่าน chips จริงก่อนแก้ (before), verify ที่ระดับ modal (chips + ตัวนับ "N out of 3 placements") **ก่อน** Save and Continue — mismatch = ทิ้งหน้าโดยไม่เซฟ, เช็ค validation error ("error(s) to continue") ทุกจังหวะ, publish แล้วต้องเห็นคำยืนยัน
- **Budget:** mutation สูงสุด 1 ครั้ง/รอบ + Telegram ทุกผล (executed/failed/skip)
- **free_promo:** delegate ไป free_promo_auto.schedule_one (verify "Scheduled" ในตัว)
- Wire แล้ว: cron 10:15 = `--send --execute-actions`

**บทเรียนจากการ execute จริง (สำคัญมากสำหรับงานหน้า):**
1. **Signin redirect มี "title-setup" ใน query param** — เช็ค URL ต้องดู path เท่านั้น (urlsplit().path)
2. **ปุ่ม Cancel ของ modal**: `button:has-text("Cancel")` จับ dialog "unsaved-changes" ที่ซ่อนอยู่ → ต้อง JS click เฉพาะปุ่ม visible
3. **Live state ≠ listing.json**: acuarela บน KDP จริงมีแค่ 1 หมวดตื้น (ร่องรอยบั๊ก 1/3 เก่า) ทั้งที่ listing อ้าง 3 — before evidence ต้องอ่านจากจอจริง
4. **หมวดที่ leaf เป็น stop-word ("General") drive ไม่ได้** — matcher ใน kdp_categories ตัดคำ general ทิ้ง → "Painting > General" เลือกไม่ได้ ห้ามใช้เป็น target
5. **Placement ใต้ parent เดียวกันรวมเป็น accordion แถวเดียว** — chips 2 แถว แต่ placements=3 คือปกติ; verify ต้องยึดตัวนับ placements + parent coverage
6. **Attempt limit = 3** ต่อ action; ถ้าหมด → experiment ค้าง failed; ทางกู้ = ปิด inconclusive + เปิด cycle ใหม่ (ทำแล้ว 2 ครั้งวันนี้: exp 4 → exp 5)

**ผลจริง:** exp 5 (cycle 3) executed + verified สำเร็จ — acuarela เปลี่ยนจาก 1 หมวดตื้นเป็น 3 leaf เต็ม (Crafts&Hobbies>Painting + Instruction&Reference>Study&Teaching + >Color), republish แล้ว มี screenshot ยืนยัน "Congratulations...submitted" ที่ `logs/action-shots/`, ราคาคงเดิม $4.49, cooldown ถึง **14 ก.ค.** แล้ว evaluate อัตโนมัติ. Suite **154 passed**. Service healthy.

**ขอบเขตที่ยังไม่ auto (ตั้งใจ):** การ*เสนอ* experiment ใหม่ (ค่า proposed) ยังต้องมนุษย์/AI session คิด — gates กันค่าเลว แต่การออกแบบ hypothesis ที่ดีต้องใช้วิจารณญาณ; นโยบาย 90 วัน (ห้าม paid, พัก generation) คงเดิม.

# 2026-07-11 (ค่ำ) — Experiment Proposer: ระบบคิด experiment ใหม่เอง (ปิด gap สุดท้ายของ full-auto)

**บุ๋ยสั่ง: "การคิด experiment ใหม่ ให้ AI คิดภายใต้ skill และกรอบที่เรา setup ไว้ — ทำได้ auto"**

**สร้าง `scripts/experiment_proposer.py`** — deterministic proposer (ไม่ใช้ LLM เดาค่า — นั่นคือต้นเหตุค่าอันตรายเดิม):
- **แหล่งข้อเสนอ:** (1) category_update จาก listing ที่มี path ไม่อยู่ใน tree จริง — ค่าที่เสนอต้องเป็น structural relative + overlap กับ title/keywords ≥2 คำแบบ non-generic (2) free_promo สำหรับเล่ม Enrolled ที่ไม่เคยโปรโม
- **หลักการสำคัญ: ไม่มั่นใจ = ข้าม ไม่เดา** — คัด "Prostate Health"/"PTSD"/"Computer Mathematics" ที่ token หลอก (คำ generic: health, stress, computer) ออกด้วย GENERIC_TOKENS blocklist + คิดจาก title/keywords เท่านั้น (ไม่ใช่หมวดตัวเอง)
- Path มี "Kindle eBooks >" prefix = recording artifact ไม่ใช่ปัญหาจริง — ไม่เปลืองสล็อต; ทุก target ที่เหลือของเล่มต้อง drivable (ไม่งั้น executor abort เปล่าๆ)
- ทุกข้อเสนอผ่าน validate_action **ตัวเดียวกับ executor** ก่อนสร้าง + กันเสนอค่าซ้ำที่เคยลอง (already_tried) + ปิด experiment ที่ fail ครบ 3/3 อัตโนมัติ (close_exhausted_failures)
- **Caps:** สร้างใหม่ ≤1/วัน, active รวม ≤3 — คิวปัจจุบัน: ฟรีโปรโม 11 เล่ม (หมวด 0 เพราะเคสที่เหลือต้องใช้วิจารณญาณ → คงไว้ให้คน)
- Wire ใน daily main() หลัง run_daily (เฉพาะโหมด --execute-actions), พังไม่ทำ daily ล่ม

**🐛 บั๊กใหญ่ที่เจอ+แก้:** run_daily เดิมประมวลผลเฉพาะ slug ใน APPROVED_EXPERIMENTS → experiment ที่ proposer สร้าง**จะค้าง planned ตลอดกาล**. แก้: เพิ่ม `load_all_experiments()` ใน profit_agent แล้วให้ daily โหลดทุกแถวจาก DB + regression test. (เทสต์ capacity เดิมต้องปรับ scope เพราะ registry ใหม่เห็นทุกแถว)

**ผลจริง:** proposer สร้าง exp 6 (ai-creative-workbook-italian ฟรีโปรโม 2 วัน) → daily รอบถัดมาขยับเป็น ready แล้ว; พรุ่งนี้ 10:15 executor ตั้งโปรโมจริง. Suite **162 passed**. Service healthy.

**Lifecycle เต็มตอนนี้ (ไม่มีมือคนใน loop):** proposer เสนอ (จากข้อมูลจริง+gate) → daily ขยับ → executor ทำบน KDP + verify ผลจริง → cooldown → evaluate → ปิด won/lost/inconclusive → proposer เติมคิวถัดไป → Telegram รายงานทุกจุด. งานที่จงใจเหลือให้คน: เคสหมวดที่ semantic กำกวม (ระบบ skip ให้เอง) + นโยบาย 90 วัน.

# 2026-07-11 (ค่ำ 2) — KPI bar Actual vs Plan บนหน้า /profit

บุ๋ยขอ KPI bar actual vs plan บนแดชบอร์ด /profit. เพิ่ม `_profit_kpi_plan()` ใน app.py + section ใหม่ใน templates/profit.html:
- **แหล่ง plan เดียว ไม่ซ้ำซ้อน:** import `DEFAULT_PLAN_TARGETS` + `_metric` จาก distribution_report (เป้ารอบเรียนรู้ที่บุ๋ยอนุมัติ 10 ก.ค.: $25 / 120 orders / 500 KENP ถึง checkpoint 31 ก.ค. จาก strategy_timeline.json)
- **Actuals จาก verified ledger เท่านั้น** (ตามปรัชญาหน้านี้) + แถบที่ 4 = Profit A vs break-even $0
- 4 แถบ ณ ตอนนี้: Royalties $7.63/$25 (31% early เหลือง), Orders 256/120 (100% on_plan เขียว), KENP 361/500 (72% watch เหลือง), Profit A −$1.27/$0 (behind แดง)
- เทสต์ใหม่ใน test_profit_api.py; suite **163 passed**; screenshot ยืนยันหน้าจริงสวยครบ

# 2026-07-11 (ค่ำ 3) — KPI bar เพิ่ม filter รายวัน/รายเดือน/90 วัน + ยุบแผนเหลือชุดเดียว

บุ๋ยขอ filter daily/month แล้วสั่งเพิ่ม: ชุดเป้ารอบเรียนรู้เดิม (ถึง 31 ก.ค.) ถูกแทนด้วยระบบ 90 วันแล้ว ให้ปรับไม่ให้ซ้ำซ้อน →
- **แผนเหลือชุดเดียว = รอบ 90 วัน (11 ก.ค. → 9 ต.ค. อ่านจาก policy_modes จริง):** `DEFAULT_PLAN_TARGETS` ใน distribution_report เปลี่ยนเป็นยอดทั้งรอบ $75 / 360 orders / 1,500 KENP / 300 free downloads (= pace เดือนละ $25/120/500 เดิม ×3) + `strategy_timeline.json` checkpoint → 2026-10-09 (กระทบ /distribution/monitor ด้วย = สอดคล้องกันทั้งระบบ)
- **หน้า /profit KPI มี toggle 3 มุมมอง:** รายวัน (delta snapshot วันต่อวัน vs เป้า/90) · รายเดือน (MTD vs เป้า/3) · 90 วัน (ยอดสะสมทั้งรอบ vs เป้าเต็ม)
- รายวัน: Profit A วัน = รายได้วัน − ต้นทุน manual ที่เกิดวันนั้น (ไม่นับ ingest cost report เก่า) ; วันแรกไม่มี snapshot ก่อนหน้า → delta = ยอดสะสม (label บอกชัด)
- ⚠️ checkpoint 31 ก.ค. ในฐานะ "วันตัดสิน ADHD ads" ยังอยู่ (cron checkpoint_20260731.py แยกเรื่องกัน ไม่ได้แตะ)
- Suite **164 passed**; QA screenshot ครบ 3 มุมมอง; toggle กดจริงบนหน้า

# 2026-07-11 (เย็น 4) — 🔴 acuarela ถูก KDP reject "disappointing customer experience" + gate กันซ้ำ

**เกิดอะไร:** เล่ม acuarela-para-principiantes (ASIN B0H6TCJ7CQ, LIVE, ขายจริง 20 orders/$0.68) — auto-executor exp5/cycle3 **republish เพื่อเปลี่ยนหมวด** วันนี้ 12:33 → ทุก republish = ส่งกลับเข้า Amazon content review → 16:37 Amazon reject "won't be accepting... might result in a disappointing customer experience."

**Root cause 2 ชั้น:**
1. **Trigger:** executor เอาเล่ม LIVE ที่ทำเงินอยู่ไป republish เพื่องานสวยงาม (หมวด) — เปิดช่องให้ review 2026 (เข้มขึ้น) reject ทิ้งทั้งเล่ม
2. **Content:** paperback 55 หน้า **0 รูปในเนื้อ** (epub มีแค่ปก) — หนังสือสอนวาดสีน้ำ (visual skill) ที่ไม่มีภาพสาธิตเลย = ตรงนิยาม disappointing ของ Amazon. สแกนแล้ว **40/40 เล่ม LIVE เป็น text-only ≤1 รูป** — นิช visual (สีน้ำ/vocab เด็ก/คู่มือสมาร์ทโฟน) เสี่ยง reject สูงถ้าโดน re-review

**แก้กันซ้ำ (โค้ด):** `scripts/kdp_action_executor.py` validate_action — category_update บนเล่ม `live_status==LIVE` = **refused** (category experiment รันเฉพาะเล่ม DRAFT). proposer ใช้ gate ตัวเดียวกัน (บรรทัด 241) → กันทั้งเสนอ+execute. +2 tests. Suite **166 passed**. ไม่ต้อง restart (cron อ่านไฟล์สด).

**รอบุ๋ยตัดสิน:** (a) ปล่อยเล่ม acuarela ตาย/unpublish — แนะนำ (ROI $0.68 ไม่คุ้ม rebuild) (b) reply "error" — ไม่ช่วยจริง (c) ทำภาพสอน step-by-step ใหม่แล้ว resubmit — เฉพาะถ้าบุ๋ยอยากได้ niche สีน้ำ. กฎใหม่: นิช visual ต้องมีภาพก่อน publish

# 2026-07-11 (ค่ำ 5) — รีวิวรอบสอง acuarela rejection: ยืนยันข้อเท็จจริง + อุดรู gate

ตรวจซ้ำตามคำสั่งบุ๋ย: (1) **ยืนยัน** email = ebook republish จริง — paperback 4 เล่มที่เคยส่งไม่มี acuarela; timeline 12:33 republish → 16:37 reject สอดคล้อง (2) **หน้า Amazon B0H6TCJ7CQ = HTTP 404** — เล่มหลุดจากร้านจริง (3) 🔴 **block ครั้งที่ 2 ของบัญชี** — ครั้งแรกคือ high-protein-meal-plan-french (BLOCKED, นิช diet) → account health = ข้อพิจารณาหลัก ห้าม appeal มั่ว/resubmit เดิมๆ (4) **อุดรู gate**: เกณฑ์ LIVE อย่างเดียวมีรู 3 เล่ม (asin มีแต่ live_status None/UNKNOWN/BLOCKED) → เปลี่ยนเป็น **refuse ถ้ามี asin** (เคย publish = ห้าม auto-republish) เทสต์ครอบ 3 เคส. Suite 166 passed.

**นัยยะที่แจ้งบุ๋ยแล้ว:** เลน category experiment ของ 90-day agent = ปิดโดยปริยาย (ทุกเล่มมี ASIN, generation หยุด) เหลือเลน free_promo; งานค้างที่ต้อง republish (subtitle 12 เล่ม, cover regen 39 เล่ม) = ควรพับ/ทำเฉพาะจำเป็น เพราะทุก republish คือทอยลูกเต๋า review (ปก 3ก.ค. เคยผ่าน แต่วันนี้พิสูจน์ว่าไม่การันตี)

# 2026-07-11 (ค่ำ 6) — Execute คำตัดสินบุ๋ย (acuarela ปล่อยตาย) + เดินหน้าเป้า 90 วัน

**บุ๋ยอนุมัติ: ปล่อย acuarela ตาย ห้าม appeal/resubmit, พับงาน republish, ปกป้องบัญชี.** ทำแล้ว:
1. ปิด exp 5 (acuarela cycle:3, cooldown) ใน DB = inconclusive + reason block/404 (closed_by claude-acuarela-block-cleanup) — กัน evaluate 14 ก.ค. บนเล่มตาย
2. listing.json acuarela → live_status=BLOCKED + key `blocked` บันทึกเหตุ+คำตัดสิน (roster 08:45 พรุ่งนี้จะ verify ซ้ำ)
3. เช็คแล้ว Reddit schedule + Pinterest pins ไม่มี acuarela — ไม่มีระบบไหนโปรโมทลิงก์ตาย
4. **จอง Free Promo easy-taxes (Declaración de la Renta) 29 ก.ค.-2 ส.ค. สำเร็จ** — ปฏิทิน stacked promo ที่บุ๋ยอนุมัติ 5 ก.ค. ครบ 4/4 เล่มฮีโร่แล้ว (15-19 workbook-es, 22-26 focus-es, 25-26 German, 29-2 taxes). ใช้ `free_promo_auto.py --only --force --start --days` (dry-run ก่อน, screenshot "Success! Your promotion was added" ยืนยัน). ⚠️ memory เก่าที่ว่า free_promo_auto รันทันทีเมื่อ import = outdated — ตอนนี้มี main guard + args แล้ว
5. สร้าง **`/root/libra/CLAUDE.md`** กฎถาวร: ห้าม republish เล่มมี ASIN, บัญชีมี 2 blocks, เลนปลอดภัย 4 เลน, นิช visual ต้องมีภาพ

**เสนอบุ๋ย (รอตัดสิน): เพิ่มเลน price experiment** แทนเลน category ที่ปิด — ปลอดภัย (หน้า pricing ไม่ trigger review), ใช้ set_price.py ที่มี safety gate อยู่แล้ว, gate: band $2.99-9.99 + 70% plan + ห้ามช่วงโปรโม + ≤1 mutation/รอบ

# 2026-07-11 (ดึก) — เลน Price Experiment (บุ๋ยอนุมัติ "เอา") — แทนเลน category ที่ปิด

**สร้างเลนทดลองราคาครบวงจร (ปลอดภัย — หน้า pricing ไม่ trigger content review):**
- **Executor** (`kdp_action_executor.py`): kind ใหม่ `price_update` — validate_price_action: band $2.99-9.99 เท่านั้น, LIVE เท่านั้น, no-op refuse, โปรโมคลุมวันนี้ refuse (royalty lock 35%); `_execute_price` delegate ไป `set_price.py` (มี 35%-abort gate + publish confirmation ในตัว) + verify listing.price เปลี่ยนจริง
- **Cooldown**: evaluation_kind "price" = 14 วันแบบ commercial (แก้ 3 จุด: profit_agent + daily ×2)
- **Proposer**: `price_candidate` — LIVE + maxKENP≥50 จาก feedback-history + royalties≤$1 + ราคา>2.99 → เสนอ $2.99; ราคาอ่านจาก listing.price → fallback recommended_price_usd (ไฟล์เดียวกับที่ kdp_upload ใช้ตั้งตอนแรก); **ห้ามคร่อมโปรโม 14 วัน** (one variable per window); + gather ข้าม live_status=BLOCKED ทุก kind (กันเสนองานให้เล่มตาย)
- ลำดับคิว: category(ตาย) > free_promo (review engine ก.ค.) > price
- **ผลจริง**: เล่มแรกที่เข้าเกณฑ์ = adhd-workbook-german-adults (KENP 51, $6.99) แต่ถูกเลื่อนถูกต้องเพราะโปรโมฮีโร่ 25-26 ก.ค. อยู่ในหน้าต่างวัดผล → จะโผล่ในคิวอัตโนมัติ ~27 ก.ค.
- เทสต์เก่า test_unknown_kinds_stay_manual เคยใช้ price_update เป็นตัวอย่าง unsupported → เปลี่ยนเป็น cover_update
- Suite **169 passed**; executor smoke: acuarela actions เก่าโดน ASIN gate refuse ครบ, exp6 (โปรโมอิตาลี) ready สำหรับ 10:15 พรุ่งนี้; CLAUDE.md อัปเดตเลนใหม่แล้ว

## 12 ก.ค. 2026 — Signal-sufficiency gate (distribution-starved) ใน proposer
บุ๋ยถามว่าข้อมูล KDP รายวัน (sync 09:15 อ่าน This-Month cumulative ผ่าน Playwright session, ไม่ใช่ API ทางการ) พอให้ agent บริหารทันไหม. สรุปร่วมกัน: **ความถี่ข้อมูลไม่ใช่ปัญหา** (หน้าต่างตัดสินใจ = 14 วัน/สัปดาห์ อยู่แล้ว) — ปัญหาจริงคือ **volume น้อยจนเป็น noise** ($7.71 royalty จริง/เดือน, 3/44 เล่มมี order ขยับ) → คอขวด = distribution ไม่ใช่ราคา.
**ฝัง logic นี้ลง `scripts/experiment_proposer.py`:**
- `_title_traffic(history)` = max(mtd_orders) + max(mtd_kenp)/PAGES_PER_BORROW(300) = eyeballs proxy (นับ free download/borrow ด้วย เพราะเป็น "traffic" ที่ต้องมีพอถึงจะวัด conversion ได้)
- `price_candidate` เพิ่ม gate: traffic < `MIN_TRAFFIC_FOR_PRICE_TEST=10` → return None (distribution-starved = visibility problem ไม่ใช่ price → ไม่เปลือง slot จูน noise). **free_promo ยังผ่าน** (มันคือคันโยก traffic)
- `distribution_starved_titles()` + Telegram รายวัน "N เล่ม distribution-starved" + คีย์ `distribution_starved`/`_slugs` ใน run_proposer output
- ผลจริง: 3 เล่ม traffic จริง (acuarela 20.5, adhd-es 23.1, ai-toolkit 17.0) ผ่าน; **36 เล่ม LIVE ถูกตีธง starved**
- scope = proposer เท่านั้น (เลนอัตโนมัติที่อาจ spawn noise experiment); APPROVED_EXPERIMENTS seeds (adhd/ai-toolkit) มี traffic ผ่านอยู่แล้ว ไม่แตะ
- เทสต์: อัปเดต test_price_candidate (read_history ต้องมี mtd_orders) + เพิ่มเคส "starved" (KENP≥50 แต่ traffic ต่ำ → None). **47 passed** (proposer+profit_agent_daily+profit_agent)
- ค่า floor 10 = heuristic noise-floor ปรับได้ (คอมเมนต์ไว้แล้ว)

## 14 ก.ค. 2026 — Audit สมองกลบริหาร (profit agent) โดย Claude: พบ deadlock เลน free_promo
- **🔴 exp 6+7 (free_promo อิตาลี 2 เล่ม) ค้าง `ready` ตั้งแต่ 11/13 ก.ค. ไม่เคยถูก execute** (agent_actions ไม่มีแถว execution เลย, log 12-14 ก.ค. ยืนยัน 3 รอบ)
- **Root cause:** `libra_profit_agent_daily.py:255-258` — status `ready` ต้องผ่าน `_title_attribution_complete` = ASIN ต้องมีแถวใน `kdp_title_attribution` ของ snapshot ล่าสุด. แต่ KDP overview รายงาน per-title เฉพาะเล่มที่มียอดขยับ → เล่มยอดศูนย์ (เป้าหมายของเลน free_promo พอดี) ไม่มีวันมีแถว → **deadlock ถาวรโดยโครงสร้าง**. ยังไม่แก้ (แตะ gate ต้องบุ๋ยอนุมัติ)
- **ผลรวม 3 เลนของ agent ตอนนี้:** category = ปิด (ASIN gate หลัง acuarela), free_promo = deadlock ข้างบน, price = ไม่มีเล่มผ่าน traffic≥10 ที่อยู่นอกหน้าต่างโปรโม → agent รันทุกวัน+ส่ง Telegram แต่ **ไม่มี mutation ใดเกิดขึ้นได้เลย**
- **ข้อมูลสำคัญจาก 17 free promo ที่จบแล้ว (ก.ค.):** รวม 35 downloads; 13/17 เล่ม = 0 downloads. เล่มที่ได้: acuarela 19, remote-workers 9, ai-productivity-es 5, adhd-es 2. → **naked free promo (ไม่มีตัวขยาย traffic ภายนอก) ≈ ตาย** และเปลืองโควตา 5 วันฟรี/เทอม
- **KPI 90 วันบน /profit นับเกิน:** มุมมอง "90 วัน" ใช้ snapshot This-Month cumulative ($10.00) ซึ่งรวมเงิน 1-10 ก.ค. ($7.63 ก่อนเริ่มรอบ 11 ก.ค.) — ควรหัก baseline เดือนแรก
- ตัวเลขจริง 14 ก.ค.: MTD royalties $10.00 / orders(ทุกชนิด) 291 / KENP 405; contribution +$1.10 (costs $8.90, 3 เล่ม estimated → cost gate closed ถูกต้อง); starved 36/40 เล่ม
- ปฏิทินฮีโร่จองครบจริง: workbook-es 15-19, focus-es 22-26, german 25-26 (คู่ LovelyBooks), taxes 29 ก.ค.-2 ส.ค.

## 14 ก.ค. 2026 (ดึก) — บุ๋ยอนุมัติ "ทำตามคำแนะนำได้เลย" → แก้ครบ 3 จุด + เจอโปรโมผี exp6

**1. แก้ deadlock attribution gate (ครบวงจร):**
- `kdp_sales_sync.py`: ledger snapshot บันทึกรายเล่มจาก **merged monthly baseline** แทน widget top-N รายวัน (`merged_title_rows`; merge เก็บ currency ด้วย) — เล่มที่เคยโผล่แล้วหลุด widget (เช่น acuarela $0.68) ไม่หายจากบัญชีอีก → unattributed remainder ลดจาก $1.51 เหลือ ~$0.86
- `profit_agent.py` `title_financial_boundary`: เล่มไม่มีแถวรายเดือน = ศูนย์ **เมื่อ remainder ของ snapshot เดือนนั้น ≤ `ATTRIBUTION_ABSENT_ZERO_BOUND_USD` ($2.00)**; เพิ่มฟิลด์ `attribution_bound_usd` บันทึกความไม่แน่นอน (เหตุผลตัวเลข: widget=topEarningTitles top-N เท่านั้น; KENP หางยาวนอก widget ≈ $1.5 ปัจจุบัน)
- `libra_profit_agent_daily.py` `_title_attribution_complete`: กติกาเดียวกัน + branch ready→execute จะ **refresh baseline flags** ที่ snapshot เดิม (baseline เก่าแช่ complete=False จะทำ evaluation inconclusive ตลอดกาล)
**2. กติกา pairing:** `validate_action` refuse free_promo ที่ไม่มีคู่ใน `data/promo_pairings.json`/`reddit_promo_schedule.json`; `_execute_free_promo` เติมคิวเตือน Reddit อัตโนมัติหลัง schedule สำเร็จ (เลือกวันแรกในหน้าต่างที่ยังว่าง). proposer ใช้ validate ตัวเดียวกัน → ไม่มี naked promo เข้าคิวได้อีก
**3. KPI /profit:** `_profit_kpi_plan` มุมมอง "ทั้งรอบ" หัก snapshot แรกสุด (entry meter-reading $7.63/256o/361p) → ตอนนี้แสดง **$2.37** จริงของรอบ. restart libra.service แล้ว
**4. รันจริงพิสูจน์:** exp6 หลุดจาก ready → executor เปิด KDP จริง → **abort เพราะ title ไม่ตรง** → สอบต่อพบ (a) listing.json title เก่าผิดจากเล่มจริง (b) **เล่มนี้เคยแจกฟรี 4-6 ก.ค. แบบไม่มีบันทึกใน listing** (โปรโมผี = หลักฐาน naked-promo เพิ่มอีกตัว) → backfill listing (title จริง + free_promo Complete 4-6 ก.ค.) + **ปิด exp6 = inconclusive** (audited: hypothesis void, กันแจกซ้ำเปลืองโควตา 2/5 วันที่เหลือ)
- exp7 (postpartum-anxiety-it) ตรวจหน้า KDP แล้ว **สะอาด ไม่เคยโปรโม + title ตรง** → คงไว้, จะ execute พรุ่งนี้ 10:15 (budget 1 mutation/รอบ) → โปรโม 16-17 + คิว Reddit 17 ก.ค. อัตโนมัติ
- เทสต์: แก้ 4 ตัวที่ encode พฤติกรรมเก่า + เพิ่ม 9 ตัวใหม่ (deadlock regression, pairing refuse/accept, merged rows, boundary bound, KPI window) → **178 passed**
- Registry หลังปิด exp6: active 2 (exp2 cooldown ประเมิน ~25 ก.ค., exp7) — slot ว่าง 1 จะไม่ถูกเติมจนกว่ามีเล่ม paired (by design)
- ⚠️ บทเรียน: **listing.json เชื่อไม่ได้เรื่องประวัติโปรโม** — เช็ค promotion-manager จริงก่อนเสมอ (เขียนลง CLAUDE.md แล้ว)
## 2026-07-18 — Profit-Pace Agent deployed

- Added `profit_pace.py`: 110% internal stretch target (`$82.50` vs approved `$75`), elapsed pace, variance, recovery/critical/ahead modes, real 7/14-day run rates, required daily revenue, and Day-90 projection.
- Added evidence-based portfolio allocation (`70% exploit / 20% explore / 10% archive`) and winner fast lane. Live state: exploit 5, explore 4, archive 31; winner watch = `ai-workflows-accountants-pt`, `beginner-watercolor-spanish`.
- Fixed pace accounting across KDP calendar-month resets and excluded snapshots before the persisted 90-day mode start. Stale data produces `insufficient_data`.
- Tightened free-promo distribution: proposer and executor require an external `post_url` or `post_id`; pairing declarations and `reminded_at` are not publication evidence.
- Blocked/non-LIVE titles are excluded from allocation and opportunities. No paid spend, new-title generation, or published-ASIN metadata/category safety gate was weakened.
- Production after deploy: verified royalties `$12.59`, contribution Profit A `$3.69`, mode revenue `$4.96`, pace `recovery`, variance `-$0.87`, required stretch pace `$0.93/day`, projected Day-90 `$62.82` at current 7-day run rate.
- Verification: `188 passed`, py_compile passed, `libra.service` active, production API verified.
## 2026-07-18 — Permanent autonomous-management rule

- Bui confirmed: automate anything Libra can do with high confidence and directly verifiable evidence; never guess.
- `executed` requires a verified external result such as KDP/API response, before/after state change, report/transaction, or external `post_url`/`post_id`.
- Reminder/digest/queue/click/process success alone is not proof. Stale, incomplete, OTP/CAPTCHA/login-blocked, or unverifiable work must stop as `manual_required`/`insufficient_data` and notify Bui.
- Account-safety, no-paid, experiment-cap, and no-republish rules always override automation.
- For workflows without a reliable API, Bui requires Claude-for-Chrome-style browser automation: inspect the live page, act, wait, and verify the live after-state. Record page/URL, before, after, confirmation, and screenshot when needed; a click alone is never success.
## 2026-07-18 — Browser-native price evidence

- Upgraded the active `price_update` lane so `scripts/set_price.py` returns evidence read from the live KDP browser session: pricing URL, input value before the edit, target value, publish confirmation text/URL, timestamp, and post-confirmation screenshot.
- `scripts/kdp_action_executor.py` now builds `verified_state_change` from that browser evidence instead of reading `listing.json` as proof. Missing confirmation URL/evidence fails the action.
- Kept the existing CLI and boolean caller contract backward compatible. No live price mutation was triggered during this change.
- Verification: `189 passed`; py_compile and diff check passed.
## 2026-07-22 — Risk-aware Recovery Agent หลัง KDP ถอดหมวด 3 เล่ม

- KDP ส่ง category quality notice และถอดหมวดที่ไม่เกี่ยวข้องจาก 3 ASIN โดยหนังสือยังขายต่อ: `B0H5C6PCBL / AI & Semantics`, `B0H6H2D17K / Career Counseling eBooks`, `B0H4KT12GV / Business Intelligence Software`.
- Root cause ของ false green: `category_health_manager.py` เดิมตรวจ taxonomy/language และกฎเฉพาะบางคำ แต่ไม่มี registry ของ KDP notices จึงรายงาน `ok` ทั้งที่ KDP พบ semantic mismatch.
- เพิ่ม `data/kdp_metadata_incidents.json`; category health แสดง `metadata_risk`, active incidents และ blacklist หมวดที่ KDP ถอด พร้อม Telegram เมื่อ signature เปลี่ยน. Production report เปลี่ยนจาก `ok` เป็น `metadata_risk`, blockers=0, warnings=26.
- เพิ่ม revenue-stall signal: 3 วันล่าสุด royalties โตเพียง `$0.15` (< `$0.25`) จึง `active=true`. Profit agent เพิ่ม `metadata_safety=closed` และปิดเฉพาะ category/metadata mutation; observation/evaluation/organic lanes ยังทำงาน.
- ไม่แก้ listing LIVE และไม่เรียก `--execute-actions`. Production pace วันที่ 22 ก.ค. = critical, actual mode revenue `$6.17`, projected 90-day `$49.73` เทียบเป้า `$75`.
- Verification: focused red/green ผ่าน; full `PYTHONPATH=. pytest -q` = `194 passed`; runtime category health + profit state ยืนยัน signal จริง.

## 2026-08-01 — Reddit post easy-taxes ถูก mod ลบ (ตรวจพบระหว่างเก็บหลักฐาน pairing)
- บุ๋ยโพสต์เล่มภาษีสเปนลง r/FreeEBOOKS จริง 29 ก.ค. 13:11 UTC (u/WK_Bui_Books, post id 1v9vtd5) แต่ถูก mod/automod ลบ — ยืนยัน 3 ชั้นผ่าน Reddit RSS (user feed + post feed = "[removed]", ไม่อยู่ใน sub new feed 100 โพสต์) 1 view/0 comment หลัง 2 วัน
- บันทึกใน data/reddit_promo_schedule.json เป็น attempted_post_url + post_result=removed_by_moderator — **จงใจไม่ใส่ post_url** เพื่อไม่ให้ pairing gate นับ distribution ที่ไม่มี traffic จริง (commit 8a120c9)
- โพสต์เก่า 8 ก.ค. (ai-prompts-freelance-designers, id 1uqr7lk) ยังมองเห็นสาธารณะ → บันทึก post_url เป็นหลักฐาน valid ย้อนหลัง
- เทคนิคเวิร์กอราวด์: Reddit บล็อก IP เซิร์ฟเวอร์ (403 ทุกหน้า HTML/JSON) แต่ **RSS (.rss) เข้าได้** — ใช้ /user/<name>/submitted.rss, /comments/<id>/.rss, /r/<sub>/new/.rss (ระวัง 429 ให้เว้นจังหวะ)
- ค้างตัดสินใจ: โปรโม easy-taxes จบ 2 ส.ค. — ท่อ traffic รอบนี้ตายแล้ว; แนวทางเสนอบุ๋ย = เช็ค inbox Reddit หาข้อความ mod / message mods ขอ approve; สาเหตุน่าจะ karma ต่ำ (1) + บัญชีอายุ 26 วัน

## 2026-08-22 — TOTAL KDP FREEZE บังคับใช้ในโค้ดจริง + เลน staging แยกขาด (claude รับช่วงจาก codex)
- เดิม freeze เป็นแค่ข้อความใน CLAUDE.md — ตอนนี้เป็น `kdp_freeze.py` ที่รันได้: `assert_kdp_mutation_allowed()` raise เสมอ, ไม่มี env override/force/วันหมดอายุ (มีเทสต์ AST ยืนยันว่าโมดูล import แค่ `dataclasses`)
- ปิดประตูครบ: Python mutator 16 ตัว (upload/cover/metadata/content/finish_publish + aplus_upload, aplus_resume_submit, set_price, free_promo_auto.schedule_one, kdp_unpublish, kdp_live_replace, kdp_fix_book, kdp_fix_publish, kdp_paperback_upload, kdp_enroll_v2, author_photo_upload, author_url_retry); HTTP 423 (approve-kdp / request-approval / status=ready, archived ยังผ่าน); `process_kdp_queue.sh` exit 73 ก่อนอ่านคิว; `watchdog.sh` เลิกตั้ง status=ready → `staged_freeze` + `publish_blocked`
- `validate_action` = ด่าน freeze ก่อน แล้วค่อยเรียก `validate_action_rules` (กฎ category/ASIN, price band, promo pairing ยังมีเทสต์ครบ ไม่ได้ถูกลบ)
- เทสต์เดิมที่พฤติกรรมเข้าไม่ถึงแล้วถูก mark skip พร้อมเหตุผล (queue rotation 6 ตัว, experiment proposer 2 ตัว) — ไม่ได้ลบทิ้ง เพื่อเป็นสเปกถ้าปลด freeze ในอนาคต
- เลน staging ใหม่: `staging_pipeline.prepare_pilot()` + `scripts/prepare_kdp_pilot.py` + สเปกตายตัว `data/pilots/senior-smartphone-fr.json` — เขียนเฉพาะใต้ `/root/kdp-staging/`, snapshot `queue.txt`+live tree ก่อน/หลัง ถ้าเปลี่ยน = `StagingBoundaryError`, จบที่ `staged_quality_passed` + manifest
- `validate_book(..., root=, require_visuals=True)` ใหม่: นิช visual ต้องมีภาพ ≥12 + `image-provenance.json` ครบทุกรูป (ห้ามมี personal data, ต้องมี alt text, ไฟล์ต้องอยู่ใน images/ เท่านั้น)
- `review_book(slug, root=)`, `build_paperback_pdf(slug, root=)`, `step4_create_files(..., output_root=)`, `write_book_from_topic(topic, output_root=, preparation_only=True)` — ทั้งหมดรับ root ชัดเจน ไม่เดา KDP_DIR; เขียนลง live root = โดน freeze guard
- run_dry_run ของ writer เดิม fail เมื่อมี cron libra ใดๆ ทำงาน → เปลี่ยนเป็นเช็คเฉพาะ cron ที่มิวเทตได้ (auto-generate / process_kdp_queue / kdp_upload / kdp_finish_publish) เพื่อให้ cron read-only (sales sync/roster/session) ไม่ทำ staging ล้มโดยไม่มีเหตุผล
- **ยังไม่ได้รันสร้างหนังสือจริง** — รันแค่ `--dry-run` (PASS, ไม่เขียนไฟล์เลย, /root/kdp-staging ยังไม่มี). `--execute` = เผา API จริง ต้องรอบุ๋ยสั่ง
- ยืนยัน: cron `auto-generate.sh` + `process_kdp_queue.sh` ยัง PAUSED (ไม่แตะ crontab); ไม่มี KDP/Playwright/Telegram action เกิดขึ้นระหว่างทำงานนี้
- Verification: `pytest tests/ -q` = 620 passed / 8 skipped / **2 failed ที่พังอยู่ก่อนแล้ว** (test_profit_api::test_primary_dashboard_exposes_verified_royalties, test_libra_profit_agent_daily::test_bounded_attribution_gap — ยืนยันด้วย git stash ว่าไม่ได้เกิดจากงานนี้ ยังไม่ได้แก้)

## 2026-08-22 (บ่าย) — วัด demand จากยอดขายจริงครั้งแรก: `demand_analysis.py`
- บุ๋ยสั่ง: สินค้าใหม่ต้องมาจากการวิเคราะห์ยอดขายจริง ไม่ใช่หัวข้อที่เดาเอา + การตลาดต้องปลอดภัยไม่เสี่ยงบล็อก
- สร้าง `demand_analysis.py` (deterministic 100% ไม่เรียก LLM — มีเทสต์ห้าม import openai/httpx) อ่าน `kdp_title_attribution` + `listing.json` จริง
- **กับดักที่เจอ**: KDP snapshot เป็นยอดสะสม month-to-date — โค้ดเดิมถ้าบวกรายวันจะได้ $325 แทนที่จะเป็น $25.58 (เฟ้อ ~13 เท่า) ต้องใช้ MAX ต่อ (asin, month) แล้วค่อยบวกข้ามเดือน
- **กับดักที่ 2**: จัดกลุ่มตาม Amazon category ไม่ได้ เพราะชื่อหมวดเป็นภาษาท้องถิ่นต่อ marketplace ("Informatique et Internet", "Negocios y dinero", "本") → คลัสเตอร์แตกเป็น 29 ก้อนไร้ความหมาย. แก้ด้วย THEME_RULES (keyword ต่อ slug ประกาศชัดในโค้ด + บันทึกว่า match คำไหน ตรวจย้อนได้)
- ผลจริง: $25.58 / 63 เล่ม, 31 จาก 38 LIVE ได้ $0; anxiety 12 เล่ม=$0, ภาษีสเปน 8 เล่ม=$0, ai_productivity 20 เล่ม=$5.51; ธีมที่มีสัญญาณ art_craft/adhd/senior_tech/kids_language (n เล็กทั้งหมด)
- **ข้อค้นพบที่สำคัญที่สุด**: `hub_events` = 0 แถว → ไม่เคยมีคลิกจากช่องทางเราเลย ⇒ ยอด $0 อาจแปลว่า "ไม่มีคนเห็น" ไม่ใช่ "ไม่มี demand". ผลิตเล่มใหม่โดยไม่แก้ distribution = ทำซ้ำความผิดเดิม
- **ข้อค้นพบที่ 2**: ทุกเล่มมีภาพ 0 รูป รวมเล่มสอนวาดสีน้ำที่โดนบล็อก → ตรงกับเหตุผล disappointing customer experience
- `product_opportunities()` จัดอันดับด้วย **รายได้ต่อเล่ม LIVE** ไม่ใช่รายได้รวม (ไม่งั้น ai_productivity 20 เล่มจะดูดีทั้งที่ $0.50/เล่ม); ตัดธีมที่หลักฐานมาจากเล่ม blocked ล้วน และตัด diet/meal-plan ถาวร
- ยังไม่ได้ตัดสินใจ: ธีมสินค้าใหม่ / ปลายทางขาย (KDP บัญชีเดิม = เสี่ยงบล็อกครั้งที่ 5 ปิดบัญชี vs Payhip = ปลอดภัย) / ช่องทางการตลาดที่ audience ตรงภาษา — รอบุ๋ยตัดสิน

## 2026-08-22 (เย็น) — ปั้นเล่มแรกที่มีภาพจริงสำเร็จใน staging: aquarelle-botanique-debutants-fr
- บุ๋ยสั่ง: ทำตามข้อเสนอ (เชื่อมระบบภาพ + ปั้นเล่มจริงใน staging), ใช้มาตรฐานคุณภาพ/ขั้นตอนอัปโหลดที่เคยกำหนด, เลือกนิชที่คุ้มที่สุดเอง
- **นิชที่เลือก: aquarelle botanique ภาษาฝรั่งเศส** — เหตุผลจากข้อมูล: art_craft = ธีมเดียวที่ทำเงิน 2 เดือนติด ($5.70, 162 KENP) × ตลาด FR = ตลาดที่ทำเงินสูงสุดเดือน ส.ค. ($5.06) และ **ไม่มีเล่ม art/craft ภาษาฝรั่งเศสในบัญชีเลย**
- ⛔ **ห้ามทำสีน้ำภาษาสเปนเพิ่ม**: `acuarela-para-principiantes-guia-paso-a-paso` (BLOCKED) กับ `beginner-watercolor-spanish` (LIVE) **ใช้ชื่อเรื่องเดียวกันเป๊ะ** = duplicate content ในบัญชีเดียว น่าจะเป็นเหตุผลจริงที่เล่มหนึ่งโดนตีตก
- **รากปัญหา "ทุกเล่มไม่มีภาพ" เจอแล้ว 2 ชั้น**: (1) `step2c_generate_images` เป็น dead code ไม่มีใครเรียก (2) `kdp-writing-guidelines.md` เขียนไว้เองว่า "Do not insert image tags" → ขัดกับกฎนิช visual ใน CLAUDE.md. แก้ guidelines แล้วให้ visual niche = บังคับมีภาพ + ระบุ provenance ต่อ source_kind + ต้อง disclose AI ตอนอัปโหลด
- โมดูลใหม่ `illustrations.py`: วางแผนภาพจาก **หัวข้อจริงในต้นฉบับ** (LLM เลือกได้เฉพาะ heading ที่มีจริง, validate ปฏิเสธหัวข้อที่ไม่มี/back matter), เรนเดอร์ด้วย gpt-image-1, แทรกภาพในหัวข้อของตัวเอง, เขียน image-provenance.json. **ภาพพลาด 1 รูป = abort ทั้งชุด** (ห้ามปล่อยเล่มมีรูโหว่เงียบ ๆ)
- `quality_gate` รองรับ provenance 4 แบบ: ai_generated (model/prompt/generated_at), screenshot (device/os/app), photo, licensed_stock
- **บั๊กที่เจอระหว่าง QA**: `category_resolver` เลือกหมวดมั่วเมื่อคะแนนเสมอ — หนังสือสีน้ำได้หมวด "Art > Techniques > **Basketry** (จักสาน)" เพราะ Basketry/Beadwork/Quillwork/Composition ได้ 7.17 เท่ากันหมด (แมตช์แค่ parent path). แก้แล้ว: เสมอ = ปฏิเสธ คงหมวดที่เสนอไว้เดิม ดีกว่าเดา
- **บั๊กลำดับ**: seo_optimizer เดิมรัน**หลัง** editorial ทั้งที่ editorial ให้คะแนน seo_quality → คะแนน SEO ถูกตัดสินบน keywords ที่ยังไม่ปรับเสมอ (รอบแรกได้ 7/8 ตก). แก้เป็น SEO ก่อน + เขียน `editorial-review.json` ให้ quality_gate อ่านเจอ
- โหมดใหม่ `--finalize`: รันด่านซ้ำบนเล่มที่ staged แล้ว ไม่เขียนใหม่ (เขียนเล่มแพงกว่ามาก) — รอบสองผ่าน `staged_quality_passed`
- **ผล QA เปิดดูของจริง**: 11,488 คำ / 73 หน้า / ภาพ 12 รูปอยู่ในหน้าที่ถูกต้องพร้อม caption ฝรั่งเศส / EPUB มี 13 ภาพ / ปกเป็นภาพวาดสีน้ำจริงไม่ใช่ gradient / references 13 รายการมี URL / editorial 8 ทุกมิติ ไม่มี critical issue / fact checks supported
- ต้นทุนที่บันทึกได้ $0.14 (editorial+seo) — ยังไม่รวมค่าเขียน+ภาพ 12 รูป (~$0.5-1) เพราะ writer ไม่ได้เซฟ cost report ในสาย staging; แก้แล้วให้เซฟ (เล่มถัดไปจะครบ)
- **ยังไม่แตะ KDP เลย**: /root/kdp ยังมี 78 โฟลเดอร์เท่าเดิม ไม่มี queue.txt ไม่มี Playwright ไม่มี freeze ถูกปลด

## 2026-08-22 (คืน) — บุ๋ยอนุมัติอัปโหลดเล่มแรกหลัง freeze: aquarelle-botanique-debutants-fr
- บุ๋ยตรวจเล่มใน staging แล้วสั่ง "ทำปกตามมาตรฐาน + โพส KDP ตามรอบเวลาได้เลย" (คำยืนยันใหม่หลังผมเตือนความเสี่ยงบล็อกครั้งที่ 5 ไปแล้ว 2 รอบ)
- ปก: ตรวจแล้วเป็นไปตามมาตรฐาน v2 อยู่แล้ว — `detect_genre` = `creative` → template illustration-led (ภาพวาดสีน้ำ + ชื่อโซนบน), `_thumbnail_ok` = True (อ่านออกที่ 160px), 2 ฟอนต์, ไม่มี drop-shadow/ป้ายปี
- **วิธีปลด freeze ที่ใช้ (สำคัญ)**: ไม่ได้ปิด freeze ทั้งระบบ แต่เพิ่ม `APPROVED_UPLOADS = {slug: เหตุผล}` + `NEW_TITLE_ACTIONS` ใน `kdp_freeze.py` แล้วส่ง `slug` เข้า `assert_kdp_mutation_allowed(action, slug)` ทุกจุด ⇒ เล่มที่อนุมัติขึ้นชั้นได้ แต่ **38 เล่มเดิมยัง republish/เปลี่ยนราคา/แก้ metadata/เปลี่ยนปกไม่ได้เลย** (มีเทสต์ยืนยันด้วยชื่อ slug จริง)
- เทสต์กันพลาด: `test_no_live_catalogue_slug_is_ever_on_the_approved_list` อ่าน /root/kdp จริง ถ้าเผลอใส่ slug ที่มี ASIN ลง APPROVED_UPLOADS จะ fail ทันที
- เชลล์คิว: guard เปลี่ยนจาก "raise เสมอ" เป็น "มี APPROVED_UPLOADS ไหม" (ด่านต่อ slug อยู่ใน kdp_upload.py อีกชั้น); เทสต์ยืนยันว่า policy ปลอมใน PYTHONPATH แทรกไม่ได้
- คิว: `queue.txt` = 1 slug, cron เปิดที่ **09:00 + 13:00** (ย้ายจาก 02:30/06:30 เพราะ `kdp_session_ensure` รัน 08:30 — รอบเดิมจะใช้ session อายุ 18-22 ชม. แล้วล้ม)
- เล่มถูกคัดลอกจาก staging → `/root/kdp/aquarelle-botanique-debutants-fr` (staging ยังอยู่เป็นต้นฉบับ), status=ready, ใส่ `ai_content_disclosure={'text':'ai_assisted','images':'ai_generated'}` + marketplace=amazon.fr, หมวดแก้ Basketry ออกแล้ว
- `python3 quality_gate.py <slug> --require-pdf --require-editorial` = **PASS** บนสำเนาจริง
- ⚠️ **TODO หลังเล่มขึ้น LIVE**: ลบ slug ออกจาก APPROVED_UPLOADS + ปิด cron คิวกลับ + รอดูผล 2-4 สัปดาห์ก่อนเล่มถัดไป (ไม่มี auto-unlock)

## 2026-08-22 (ดึก) — เลน Payhip/Stripe เสร็จครบ 10/10 tasks (test mode)
- ติดตั้ง `stripe==15.5.1` ด้วย `pip install --break-system-packages` (เครื่องนี้ใช้ system dist-packages ทั้งหมด ไม่มี venv) + pin ใน requirements.txt
- **กับดัก SDK ที่เสียเวลาที่สุด**: `stripe.WebhookSignature.verify_header()` ฟอร์แมต payload ด้วย `%s` — ถ้าส่ง **bytes** เข้าไปมันจะเซ็น `repr` (`b'{...}'`) ไม่ใช่ body จริง → ลายเซ็นไม่มีวันตรง **ต้อง `.decode('utf-8')` ก่อนเสมอ**
- กับดักที่ 2: `construct_event()` คืน StripeObject ที่ไม่ใช่ dict (`.get()` พัง) → ใช้ `verify_header` ตรวจลายเซ็น แล้ว `json.loads(raw)` เอง ทุกอย่างเป็น dict ธรรมดา ตรวจสอบง่าย
- กับดักที่ 3: ลำดับ error ต้องเป็น **signature ก่อน freshness** — ถ้าเช็ค stale ก่อน ลายเซ็นปลอมที่ใส่ `t=1` จะถูกรายงานเป็น "stale" ทั้งที่มันคือของปลอม
- โมดูลใหม่: `stripe_webhook.py` · `commerce_reconciliation.py` · `commerce_reporting.py` · `commerce_growth.py` · `scripts/libra_commerce_reconcile.py` + routes ใน app.py + runbook `docs/runbooks/libra-commerce-test-mode.md`
- **ตรรกะเงินที่บังคับด้วยเทสต์**: Payhip paid = `payment_pending` รายได้ 0 เสมอ · Stripe verified ที่ตรง id+จำนวน+สกุล = `paid_verified` · จำนวนไม่ตรง = `reconciliation_failed` + incident · refund ย้อนรายได้เฉพาะ `succeeded` · terminal refund เปลี่ยนใจไม่ได้ (เปิด incident แทน) · refund > gross ปฏิเสธ · `balance.available` = สังเกตเฉยๆ ห้ามสร้าง fee/payout item · payout = settlement ค้างที่ `pending_reconciliation` เพราะยังไม่มีแหล่ง balance-transaction ที่ได้อนุญาต · dispute = `manual_required`
- ค่าที่ไม่รู้ = `None` + flag `*_complete: false` **ห้ามใส่ 0** (0 ทำให้กำไรดูดีเกินจริง); สกุลเงินไม่รวมกันเด็ดขาด ไม่มี `converted_total`
- `commerce_growth.py` เป็น pure function `paid_spend_minor: 0` ทุกเส้นทาง; incident เรื่องเงินตัดหน้าทุกกฎการเติบโต
- tracking: `make_tracking_token(..., destination_kind, allowed_hosts)` — allowlist แยกต่อ kind, ปฏิเสธ userinfo (`https://payhip.com@evil.example`) และ fragment; **ยังไม่แปะ click_id ต่อท้าย URL** จนกว่าจะพิสูจน์ว่ามันรอดผ่าน checkout จริง; attribution = `unknown` ไม่ใช่ 0
- Verification: `pytest tests/ -q` = **785 passed / 8 skipped / 2 failed ที่พังอยู่ก่อนแล้ว**; ไม่มีการติดต่อ Payhip/Stripe จริงเลย ไม่มีการลงทะเบียน webhook ไม่มี cron ใหม่
- สถานะ: `implemented_test_mode` — ขั้นต่อไปเป็นงานมือของบุ๋ย (สร้างสินค้าใน Payhip, KYC+บัญชีธนาคาร Stripe, เชื่อม Payhip↔Stripe, ลงทะเบียน webhook, ใส่ secret ใน .env) แล้วค่อยทำธุรกรรมทดสอบ 1 รายการ

## 2026-08-22 (ดึกมาก) — Payhip "ออโต้ 100%" เท่าที่แพลตฟอร์มอนุญาต + เจอว่า hub ลิงก์ตายมาตลอด
- บุ๋ยสั่ง "ทำ payhip ออโต้ 100%" — ตรวจข้อเท็จจริงก่อน: **Payhip API มีแค่ coupons + license keys** (payhip.com/api-reference 22 ส.ค.) ไม่มี create product / webhook / sales list → ใช้ Playwright เหมือน KDP; Stripe รองรับไทย; KYC/2FA/OAuth connect ต้องคนทำครั้งเดียว
- **ด่านใหม่ที่สำคัญที่สุด**: `payhip_catalog.guard_book_for_payhip` ปฏิเสธเล่มที่ `kdp_select.status=Enrolled` — ขาย EPUB นอก Amazon ทั้งที่อยู่ใน KU = ผิด exclusivity = เสี่ยงบัญชีอีก; เล่มเก่า 39/64 อยู่ใน Select ห้ามเอาไป Payhip; เล่ม aquarelle ใหม่ไม่ได้ enroll (uploader ไม่ enroll; kdp_enroll_v2 ถูก freeze) → ขายได้
- ไฟล์ใหม่: `payhip_catalog.py` (guard/bundle/spec/record) · `payhip_admin.py` (Playwright: session 0600, SELECTORS dict เดียว, `--inspect` dump ฟอร์มจริง, before/after evidence, click ไม่ใช่ผล) · `stripe_admin.py` (ตรวจ sk_test_ + acct, webhook endpoint idempotent, เขียน secret ลง .env ไม่พิมพ์) · `scripts/payhip_publish.py` (orchestrator) · `scripts/commerce_setup_check.py` (readiness wizard) · `scripts/commerce_daily_report.py`
- `/growth/products/{slug}` หน้าสินค้าพร้อมลิงก์ติดตามไป Payhip (payhip_outbound + click_id, attribution=unknown); `/growth/out` รับ allowlist ต่อ kind
- cron ใหม่: reconcile ออฟไลน์ทุก 30 นาที + digest Telegram 09:35 (เงียบถ้าไม่มีอะไร)
- .env: seed เองได้เฉพาะ `LIBRA_COMMERCE_MODE=test`, `PAYHIP_ALLOWED_HOSTS`, `PAYHIP_WEBHOOK_TOKEN_TEST` (generated), `LIBRA_GROWTH_TRACKING_SECRET` (generated)
- ⚠️ **ค้นพบ**: `LIBRA_GROWTH_TRACKING_SECRET` ไม่เคยอยู่ใน environment ของ libra.service (unit ไม่มี EnvironmentFile, app ไม่ export) → หน้า `/growth/books/*` บน production คืน **503 มาตลอด** = ลิงก์ Content Hub ที่เคยโพสต์ "ตาย" ⇒ hub_events=0 ส่วนหนึ่งเพราะลิงก์ใช้ไม่ได้ ไม่ใช่แค่ไม่มีคนคลิก. แก้แล้ว: app.py export จาก .env ไป os.environ (setdefault) + restart → หน้า book คืน 200 แล้ว
- dry-run จริง: `payhip_publish.py --slug aquarelle-botanique-debutants-fr --price-minor 1290 --currency EUR --dry-run` → bundle 40.9MB (PDF+EPUB+LISEZ-MOI) ที่ data/payhip-bundles/ (gitignored)
- Verification: `pytest tests/ -q` = 816 passed / 8 skipped / 2 failed เดิม; libra.service restart แล้ว active; ยังไม่ติดต่อ Payhip/Stripe จริง (ไม่มีบัญชี)
- เหลือของบุ๋ย 3 ข้อ (ทำครั้งเดียว): สมัคร Payhip → สมัคร Stripe+KYC+ธนาคาร → กด Connect Stripe ใน Payhip แล้วใส่ค่าใน .env ตาม `scripts/commerce_setup_check.py`; จากนั้น `--inspect` ยืนยัน selectors ก่อน `--execute` ครั้งแรก

## 2026-08-22 (ดึกสุด) — Payhip login ติด reCAPTCHA → เปลี่ยนเป็น human-handoff
- บุ๋ยสมัคร Payhip แล้ว ส่ง credential มาในแชท → เก็บลง `.env` (chmod 600) ด้วย write_env_value ไม่พิมพ์ออกจอ
- หน้า login จริง = `/auth/login` (ไม่ใช่ /login → 404), ช่องอีเมล `input[name='login']` (#email_affil), password `#password_f`, ปุ่ม "Log in", มี reCAPTCHA v3 + fallback v2; แก้ SELECTORS แล้ว (probe: `manual_probes/probe_payhip_login_form.py`)
- **ผล login จริง**: Payhip บังคับ "I'm not a robot" checkbox (หน้าเขียนว่า "exceeding reCAPTCHA Enterprise free quota") — บราวเซอร์ทุกตัวบนเซิร์ฟเวอร์นี้ (Playwright/MCP) IP เดียวกัน ติดเหมือนกัน; **ไม่หลบ CAPTCHA** (กฎบ้าน + ToS + เสี่ยงบัญชีใหม่โดนแบน) → driver raise `captcha_manual_required`
- ทางที่ใช้: `scripts/payhip_upload_pack.py` สร้างโฟลเดอร์ `/root/downloads/payhip-<slug>/` (zip ลูกค้า 40.9MB, cover, product-text.txt, CHECKLIST-TH.txt, webhook URL) → บุ๋ยอัปโหลดเอง ~5 นาที/เล่ม → ส่ง URL `payhip.com/b/xxxx` กลับ → `scripts/payhip_record_product.py --slug --url` **เปิดหน้าสาธารณะตรวจว่ามีชื่อเล่มจริงก่อนบันทึก** (หลักฐาน ไม่ใช่แค่ลิงก์ที่แปะมา) → หน้า /growth/products/<slug> ขึ้นเอง
- ส่วนที่ยังออโต้ 100%: bundle/สเปก/ด่าน KDP Select, Stripe webhook (API), รับเงิน/กระทบยอด/รายงาน/ตัดสินใจ, หน้าสินค้า+tracking. ส่วนที่ต้องคนครั้งเดียวต่อเล่ม: กดอัปโหลดใน Payhip (เพราะ CAPTCHA)
- ทางเลือกถ้าบุ๋ยอยากออโต้เต็ม: ล็อกอินบนเครื่องตัวเองแล้วส่ง cookie session มา (ยังไม่ได้ทดสอบว่า Payhip ยอมรับ session ข้าม IP) — ไม่ได้ทำ
- ⚠️ แจ้งบุ๋ย: อย่าส่งรหัสผ่านในแชทอีก ให้พิมพ์ใส่ .env เองหรือบอกให้ผมสร้าง prompt รับแบบไม่แสดง; รหัสที่ส่งมาควรเปลี่ยนหลังตั้งค่าเสร็จ

## 2026-08-22 (ปิดท้าย) — Payhip + Stripe เชื่อมจริงแล้ว (test mode) ท่อทดสอบผ่าน
- บุ๋ยทำครบ: สมัคร Payhip · สมัคร Stripe + KYC + บัญชีกรุงไทย (THB) · Connect Stripe ใน Payhip · วาง webhook URL + ติ๊ก paid/refunded
- Stripe account จริง: `acct_1U76cEJUy2UX3wWt` (livemode=false); webhook endpoint `we_1U77OaJUy2UX3wWtGoHpBS8J` **สร้างอัตโนมัติโดย `scripts/commerce_setup_check.py --stripe`** พร้อม 8 events; signing secret เขียนลง .env เอง
- ปรับ setup check: ดึง `acct_…` จากคีย์เอง บุ๋ยจึง copy ค่าเดียว (`STRIPE_SECRET_KEY_TEST`)
- Radar = **Lite (ฟรี)** ตามที่แนะนำ; statement descriptor เดิม "LIBRA WINAI" แนะนำเปลี่ยนเป็นชื่อที่ลูกค้าจำได้ (กัน chargeback) — ยังไม่ยืนยันว่าบุ๋ยเปลี่ยนหรือยัง
- **ทดสอบท่อจริงผ่าน public URL**: signed event → `200 {"status":"accepted"}` เก็บลง ledger เป็น `verified` / `pending_reconciliation` (`no_matching_order` = ถูกต้อง เพราะยังไม่มี Payhip order) · forged signature → `400 signature_invalid` ⇒ nginx → app → ตรวจลายเซ็น → inbox ทำงานครบ (ลบ event ทดสอบออกแล้ว)
- `commerce_setup_check.py` = **ready: True** ทุกข้อ
- ⚠️ บุ๋ยส่ง secret key มาในแชท (test key) — ควร roll คีย์นี้ใน Stripe หลังใช้งานจริงเริ่ม และห้ามส่ง live key แบบเดียวกันเด็ดขาด
- เหลือขั้นเดียว: อัปโหลดสินค้าใน Payhip ด้วยชุดที่เตรียมไว้ `/root/downloads/payhip-aquarelle-botanique-debutants-fr/` แล้วส่ง URL `payhip.com/b/xxxx` กลับมา → `scripts/payhip_record_product.py` + เติม `PAYHIP_PRODUCT_IDS_TEST` (ตอนนี้ placeholder `pending-first-product`)

## 2026-08-22 (จบวัน) — สินค้าแรกขึ้นขายจริงบน Payhip: payhip.com/b/GDRi5
- บุ๋ยอัปโหลดสินค้าเองผ่าน Claude in Chrome (ส่วนขยายในบราวเซอร์บุ๋ย — ผ่าน CAPTCHA เพราะล็อกอินอยู่แล้ว); ผมส่ง prompt สำเร็จรูปให้วางในแผง Claude ด้านขวา
- `scripts/payhip_record_product.py` **เปิดหน้าสาธารณะตรวจจริง** → `title_found: true` (646KB HTML มีชื่อ "Aquarelle Botanique pour Début…") จึงบันทึก `commerce_products` เป็น live · €12.90
- `PAYHIP_PRODUCT_IDS_TEST` เปลี่ยนจาก placeholder เป็น `GDRi5,aquarelle-botanique-debutants-fr` (ต้องมี ไม่งั้น webhook Payhip จะปฏิเสธด้วย unknown_product)
- **ทดสอบครบวงจรจริง**: หน้า `/libra/growth/products/aquarelle-botanique-debutants-fr` → 200 · คลิกลิงก์ติดตาม → 307 ไป payhip.com/b/GDRi5 · บันทึก `payhip_outbound` + click_id 32 หลัก + `attribution_status: unknown` (ลบ event ทดสอบออกแล้ว hub_events=0)
- สถานะ: `commerce_setup_check` = ready True ทุกข้อ; pytest 820 passed / 8 skipped / 2 failed เดิม
- ⚠️ ยังไม่ได้ทำ **controlled test purchase** (ซื้อจริงด้วยบัตรทดสอบ Stripe 4242…) — เป็นด่านสุดท้ายก่อนขายจริงตาม runbook; ต้องพิสูจน์ delivery → Payhip event → Stripe match → refund → payout
- ⚠️ Payhip อาจใช้ Stripe test mode ไม่ได้ (Payhip ใช้ live checkout ของตัวเอง) — ต้องตรวจว่าจะทดสอบยังไงโดยไม่เสียเงินจริง ก่อนแนะนำบุ๋ยซื้อทดสอบ

## 2026-08-22 (ค่ำ) — เปิด LIVE MODE ตามคำสั่งบุ๋ย + เตรียมทดสอบซื้อจริง
- **ผลตรวจที่ทำให้ต้องเปิด live**: Payhip **ไม่มี test/sandbox mode เลย** (เอกสาร + หน้าเว็บจริงไม่มีร่องรอย) ทุกการซื้อคือเงินจริง ⇒ webhook test-mode ที่ตั้งไว้จะไม่มีวันได้รับ event จากการขายจริง (Stripe แยก test/live เด็ดขาด)
- Stripe account จริง `acct_1U76cEJUy2UX3wWt`: charges_enabled ✓ payouts_enabled ✓ details_submitted ✓ · TH/THB · capabilities: card_payments, **promptpay_payments**, transfers
- **สิ่งที่แก้ให้รองรับ live** (TDD ทุกจุด): `settings.py` อ่านคีย์ตามโหมด (`*_TEST`/`*_LIVE`) + `expect_livemode` · `stripe_webhook` ตรวจ `livemode` ต้องตรงโหมด **ทั้งสองทาง** (test event ตอน live ก็ถูกปฏิเสธ) และ `mode` ที่บันทึกมาจาก event ไม่ใช่ config · `payhip_webhook` mode ตามคอนฟิก แต่ยัง `unverified` เสมอ · ledger รับทั้ง 2 โหมด ปฏิเสธโหมดแปลก · reporting รายงานโหมดที่อยู่ใน ledger จริง (มีทั้งคู่ = `mixed`) · reconcile CLI ต้องระบุ `--mode` ชัดเจน · `verify_account(mode=…)` ประกาศโหมดชัด ไม่เดาจากคีย์
- **Payhip coupon API ใช้ได้**: `scripts/payhip_coupon.py` — กับดัก 2 อัน (1) Cloudflare 403 error 1010 ถ้าไม่มี browser User-Agent (2) API เป็น **form-encoded** ไม่ใช่ JSON (ส่ง JSON = `required_parameters` ทุกช่องหาย)
- สร้างโค้ด **TESTRUN95** (ลด 95%, จำกัด 1 ครั้ง, ผูก GDRi5) เรียบร้อย → ซื้อทดสอบ ~€0.65 (~25฿)
- seed live config ฝั่ง Payhip แล้ว: `PAYHIP_PRODUCT_IDS_LIVE`, `PAYHIP_WEBHOOK_TOKEN_LIVE` (URL ใหม่ ต้องเปลี่ยนใน Payhip Developer tab)
- ⛔ **ยังขาดอย่างเดียว**: `STRIPE_SECRET_KEY_LIVE` (`sk_live_…`) — บุ๋ยต้องใส่ .env เอง ห้ามส่งผ่านแชท; หลังใส่แล้วรัน `scripts/commerce_setup_check.py --stripe` จะสร้าง live webhook + เขียน `STRIPE_WEBHOOK_SECRET_LIVE` ให้เอง
- pytest 837 passed / 8 skipped / 2 failed เดิม

## 2026-08-22 (ค่ำ-2) — LIVE MODE ทำงานจริงแล้ว + วิธีจัดการ sk_live ที่ปลอดภัย
- บุ๋ยส่ง `sk_live_` มาในแชท (เตือนแล้วแต่ส่งมา) → ใช้ทำงานให้จบทันที แล้ว **ลบออกจาก .env** เพราะ **runtime ไม่ต้องใช้ secret key เลย** — ระบบแค่ verify ลายเซ็นด้วย `STRIPE_WEBHOOK_SECRET_LIVE` + เทียบ `STRIPE_EXPECTED_ACCOUNT_LIVE` ⇒ secret key มีไว้ตอน setup ครั้งเดียวเท่านั้น **บอกบุ๋ยให้ roll key ทิ้งได้เลย**
- ปรับ `commerce_setup_check` ให้ readiness ขึ้นกับ webhook secret + account id (ไม่ใช่ secret key) → เก็บ key ไว้ในเครื่องนานๆ ไม่มีเหตุผล
- live webhook endpoint: `we_1U79DAJUy2UX3wWtniapHPTz` (8 events) · account `acct_1U76cEJUy2UX3wWt`
- **บั๊ก SDK ที่เจอ**: `stripe.Account.retrieve()` ไม่มี field `livemode` (มันเป็นคุณสมบัติของ request ไม่ใช่ของ account) → `verify_account` เดิม fail `mode_mismatch` ตอน live. แก้: ถ้า livemode เป็น None ให้ยึด prefix ของคีย์ (`sk_live_`/`sk_test_`) เป็นตัวชี้ขาด
- **บั๊กจริงที่เกือบทำยอดขายหาย**: `_apply_stripe_payment` ฮาร์ดโค้ด `event["mode"] != "test"` → live payment จะค้าง `unverified_payment_event` ตลอดกาล = ขายได้จริงแต่ยอดไม่ขึ้น **แก้แล้ว** + เทสต์ live payment/refund
- **พิสูจน์ท่อ live จริงผ่าน public URL**: live signed → `200 accepted` เก็บเป็น `mode=live, verified` · test-mode event → `403 wrong_mode` · ลายเซ็นปลอม → `400 signature_invalid` (ล้าง ledger หลังทดสอบแล้ว events=0 incidents=0)
- `.env` ตอนนี้: `LIBRA_COMMERCE_MODE=live`, มี `*_LIVE` ครบ, **ไม่มี** `STRIPE_SECRET_KEY_LIVE`
- ⚠️ ค้าง: บุ๋ยต้องเปลี่ยน webhook URL ใน Payhip เป็นโทเคน LIVE (`PAYHIP_WEBHOOK_TOKEN_LIVE`) ไม่งั้น event จาก Payhip จะโดนปฏิเสธ 404
- pytest 840 passed / 8 skipped / 2 failed เดิม

## 2026-08-22 (ค่ำ-3) — ⛔ พบข้อจำกัดถาวร: Payhip (GB) + Stripe ไทย เก็บค่าคอมไม่ได้
- หน้าจ่ายเงินจริงขึ้นเตือน: **"Stripe doesn't currently support application fees for platforms in GB with connected accounts in TH"**
- ตรวจแล้ว: Stripe Connect **ไม่รองรับ application fee ข้ามคู่ประเทศ GB↔TH** (Payhip เป็นบริษัทอังกฤษ, บัญชีเราไทย) — Payhip ใช้ application fee เก็บค่าคอม 5% (แผนฟรี) จึงหักไม่ได้
- ไม่ใช่บั๊กชั่วคราวและแก้ฝั่งเราไม่ได้ — เป็นข้อจำกัดระดับ Stripe/ประเทศ
- **ทางเลือกที่ตรวจแล้ว**:
  1. **PayPal ใน Payhip** — Payhip รองรับ PayPal + อีก 1 processor พร้อมกัน; PayPal เก็บค่าคอมของ Payhip ได้คนละกลไก (ไม่ใช้ application fee) ⇒ น่าจะใช้ได้ **แต่ยังไม่ได้ทดสอบจริง** และเราไม่มีบัญชี PayPal ธุรกิจ
  2. **อัปเป็น Payhip Pro $99/เดือน (0% fee)** — ถ้าไม่มีค่าคอมก็ไม่ต้องใช้ application fee (สมมติฐาน ยังไม่ยืนยัน) แต่ $99/ด. = ~3,600฿ ไม่คุ้มเลยเมื่อยอดขายยัง $0
  3. **ขายตรงด้วย Stripe Payment Links ของเราเอง** — ไม่ผ่านตัวกลาง ไม่มี application fee, เงินเข้าบัญชีเราตรง, เสียแค่ค่าธรรมเนียม Stripe; ต้องทำระบบส่งไฟล์เอง (เรามี webhook + ledger อยู่แล้ว ต่อเพิ่มไม่มาก)
  4. Gumroad / Lemon Squeezy (merchant of record) — ต้องตรวจว่ารองรับผู้ขายไทยไหม
- **ยังไม่ได้ซื้อทดสอบ** — หยุดบุ๋ยไว้ก่อนกด Buy Now; คูปอง TESTRUN95 ยังไม่ถูกใช้ (คำนวณถูก €12.90→€0.64)
- Payhip pricing: ฟรี 5% · Plus $29/ด. 2% · Pro $99/ด. 0%

## 2026-08-22 (ค่ำ-4) — ตัดสินใจย้ายไป Lemon Squeezy (merchant of record)
- ตัดทางเลือกทีละอัน: PayPal ไทยใช้ไม่ได้แล้ว (บุ๋ยยืนยัน) · Payhip Pro $99/ด. ไม่คุ้มเมื่อยอด $25/6สัปดาห์ · Xendit ใน Payhip เน้นผู้ซื้อ SEA ไม่เหมาะลูกค้าฝรั่งเศส
- **บุ๋ยเปิดหน้า docs ให้แล้ว: Lemon Squeezy รองรับผู้ขายไทย ✓** (เว็บเขาบล็อก 403 ทุก path จากเซิร์ฟเวอร์เรา — ต้องให้บุ๋ยเปิดดูแทน)
- เหตุผลที่ MoR สำคัญ ไม่ใช่แค่เรื่องค่าคอม: **ขายอีบุ๊คให้ผู้บริโภค EU ต้องเก็บ+นำส่ง VAT ตั้งแต่ยูโรแรก ไม่มีขั้นต่ำ** — ที่ผ่านมา Amazon เป็นผู้ขายตามกฎหมายจึงจัดการให้ ถ้าขายตรงเองภาระตกที่บุ๋ย; Lemon Squeezy เป็น MoR = รับผิดชอบ VAT แทนทั้งหมด
- ค่าธรรมเนียมเทียบที่ €12.90: LS 5%+$0.50 ≈ €1.11 (รวม payment processing + VAT compliance) vs Payhip+Stripe ≈ €1.40 (ยังต้องจัดการ VAT เอง) ⇒ LS ถูกกว่าและปลอดภัยกว่า
- **LS มี test mode จริง** (ต่างจาก Payhip) → ทดสอบครบวงจรได้โดยไม่เสียเงิน
- webhook LS: header `X-Signature` = HMAC-SHA256 hex ของ raw body ด้วย signing secret; payload มี `meta.event_name`, `meta.test_mode`, `meta.custom_data`, `data.attributes` (identifier UUID, subtotal, tax, total, total_usd, currency, status/status_formatted, user_email, first_order_item)
- Stripe ซื้อ Lemon Squeezy ปี 2024 → กำลังรวมเป็น Stripe Managed Payments
- **ที่ต้องเขียนใหม่**: `lemonsqueezy_webhook.py` + catalog adapter (~20% ของงาน); **ที่ใช้ต่อได้ทั้งหมด**: commerce_ledger, reconciliation, reporting, growth, routes, CLI (~80%)
- Payhip: ไม่ลบทิ้ง เก็บสินค้า GDRi5 ไว้ก่อน (ยังไม่มีใครซื้อ) — คูปอง TESTRUN95 ยังไม่ถูกใช้

## 2026-08-22 (กลางคืน) — ต่อ Lemon Squeezy สำเร็จ ท่อทำงานจริงแล้ว
- ร้าน **WKBUI** `wkbui.lemonsqueezy.com` · store id **457485** · สกุล THB · ประเทศ TH · user winai363@gmail.com
- **LS มี API เต็มรูปแบบ** (ต่างจาก Payhip): auth ผ่าน Bearer + header `Accept/Content-Type: application/vnd.api+json`; ตั้ง webhook เองผ่าน API ได้ → **ไม่ต้องใช้บราวเซอร์เลย** (แก้ปัญหา CAPTCHA ที่เจอกับ Payhip)
- สร้าง webhook **128617** ผ่าน API: events `order_created, order_refunded, subscription_*` → `/libra/api/webhooks/lemonsqueezy`; signing secret สุ่มเองเก็บใน `.env` (`LEMONSQUEEZY_WEBHOOK_SECRET`, `LEMONSQUEEZY_STORE_ID`)
- **โมเดลความจริงต่างจาก Payhip/Stripe**: LS เป็น merchant of record = เป็นผู้ขายตามกฎหมายเอง ⇒ order ที่เซ็นลายเซ็นถูกต้อง **คือ** หลักฐานเงิน ไม่มี processor ที่สองให้จับคู่ ⇒ ลายเซ็นคือด่านเดียว (HMAC-SHA256 hex ของ raw body ใน header `X-Signature`)
- **กฎภาษีที่ใส่ไว้**: `tax_minor` ที่ LS เก็บและนำส่งแทนเรา **ไม่ใช่รายได้เรา** → บันทึก gross เป็นยอดหักภาษีแล้ว และแสดง `tax_minor` แยกใน totals (เพิ่มคอลัมน์ `tax_minor` ใน commerce_orders)
- refund ใช้ id เดียว `refund:<order_id>` → replay ไม่สร้างซ้ำ; `meta.test_mode` กำหนด mode ของ event เอง (config ไม่ทับ)
- ไฟล์ใหม่: `lemonsqueezy_webhook.py` + route `/api/webhooks/lemonsqueezy` + fixtures + เทสต์ 17 ตัว
- **พิสูจน์ผ่าน public URL จริง**: signed → 200 accepted → order `paid_verified` 50000 THB slug ถูกต้อง · ลายเซ็นปลอม → 400 · ไม่มีลายเซ็น → 400 (ล้าง ledger แล้ว)
- pytest 861 passed / 8 skipped / 2 failed เดิม
- ⚠️ ค้าง: **identity verification ใน LS ยัง Action Required** (ต้องคน) — ยังรับเงินจริงไม่ได้จนกว่าจะผ่าน แต่ test mode ใช้ได้เลย; ยังไม่ได้สร้างสินค้าใน LS
