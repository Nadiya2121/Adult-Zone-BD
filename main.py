import os, io, time, hmac, hashlib, json, random, asyncio, urllib.parse
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

import httpx
from PIL import Image, ImageFilter
from bson import ObjectId
from fastapi import FastAPI, Request, HTTPException, Header, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from db import (
    init_db, get_settings, users_col, videos_col,
    unlocks_col, trending_col, bot_msgs_col, settings_col
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID", "0"))
MINI_APP_URL = os.getenv("MINI_APP_URL", "").rstrip("/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

FEED_CACHE = {"data": None, "expires": 0}
AD_SESSIONS = {}

def clear_feed_cache():
    FEED_CACHE["data"] = None
    FEED_CACHE["expires"] = 0

# ---------------- FSM STATES FOR ZERO-COMMAND UPLOAD ----------------
class UploadState(StatesGroup):
    waiting_type = State()
    waiting_thumb = State()
    waiting_title = State()
    waiting_existing_select = State()
    batch_parts = State()

# ---------------- HELPER: BLUR 16:9 THUMBNAIL + IMGBB ----------------
async def process_and_upload_thumb(file_id: str) -> str:
    file = await bot.get_file(file_id)
    bio = io.BytesIO()
    await bot.download_file(file.file_path, bio)
    bio.seek(0)

    img = Image.open(bio).convert("RGB")
    target_w, target_h = 1280, 720

    bg = img.copy()
    bg_ratio = max(target_w / bg.width, target_h / bg.height)
    bg = bg.resize((int(bg.width * bg_ratio), int(bg.height * bg_ratio)), Image.Resampling.LANCZOS)
    left = (bg.width - target_w) // 2
    top = (bg.height - target_h) // 2
    bg = bg.crop((left, top, left + target_w, top + target_h)).filter(ImageFilter.GaussianBlur(28))

    fg_ratio = min(target_w / img.width, target_h / img.height)
    fg = img.resize((int(img.width * fg_ratio), int(img.height * fg_ratio)), Image.Resampling.LANCZOS)
    bg.paste(fg, ((target_w - fg.width) // 2, (target_h - fg.height) // 2))

    out = io.BytesIO()
    bg.save(out, format="WEBP", quality=85)
    out.seek(0)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.imgbb.com/1/upload",
            data={"key": IMGBB_API_KEY},
            files={"image": ("thumb.webp", out, "image/webp")}
        )
        data = resp.json()
        return data["data"]["url"]

# ---------------- HELPER: SEND NOTIFICATION CHANNEL POST ----------------
async def send_channel_notification(video_doc: dict):
    cfg = await get_settings()
    notif_ch = cfg.get("notification_channel_id")
    if not notif_ch:
        return
    vid_id = str(video_doc["_id"])
    parts_count = len(video_doc.get("parts", []))
    caption = (
        f"🎬 <b>{video_doc['title']}</b>\n\n"
        f"🎞️ <b>Total Parts:</b> {parts_count:02d}\n"
        f"🔥 <b>Status:</b> New Upload"
    )
    buttons = [[
        InlineKeyboardButton(
            text=cfg.get("watch_btn_text", "🟢 Watch Now"),
            url=f"https://t.me/{BOT_USERNAME}?start=vid_{vid_id}"
        )
    ]]
    if cfg.get("tutorial_btn_url"):
        buttons.append([InlineKeyboardButton(
            text=cfg.get("tutorial_btn_text", "🟡 কিভাবে ভিডিও দেখবেন?"),
            url=cfg["tutorial_btn_url"]
        )])
    if cfg.get("custom_btn_url"):
        buttons.append([InlineKeyboardButton(
            text=cfg.get("custom_btn_text", "🔵 আমাদের চ্যানেলে জয়েন করুন"),
            url=cfg["custom_btn_url"]
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        if video_doc.get("thumbnail"):
            await bot.send_photo(int(notif_ch), photo=video_doc["thumbnail"], caption=caption, parse_mode="HTML", reply_markup=kb)
        else:
            await bot.send_message(int(notif_ch), text=caption, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        print("Notification Error:", e)

# ---------------- HELPER: TRACK BOT MESSAGE FOR AUTO-DELETE ----------------
async def log_bot_msg(chat_id: int, message_id: int):
    cfg = await get_settings()
    hours = cfg.get("auto_delete_hours", 6)
    if hours <= 0:
        return
    await bot_msgs_col.insert_one({
        "chat_id": chat_id,
        "message_id": message_id,
        "delete_at": datetime.now(timezone.utc) + timedelta(hours=hours)
    })

# ---------------- HELPER: AUTO FORCE JOIN CHECK (ONLY CHANNEL IDs) ----------------
async def get_missing_channels(uid: int, cfg: dict) -> list:
    missing = []
    for raw_ch in cfg.get("force_channels", []):
        ch_id = raw_ch["id"] if isinstance(raw_ch, dict) else str(raw_ch).strip()
        if not ch_id:
            continue
        target = int(ch_id) if (ch_id.startswith("-100") or ch_id.lstrip("-").isdigit()) else ch_id
        try:
            member = await bot.get_chat_member(target, uid)
            if member.status in ["left", "kicked"]:
                chat = await bot.get_chat(target)
                url = f"https://t.me/{chat.username}" if chat.username else (chat.invite_link or "")
                if not url:
                    try:
                        url = await bot.export_chat_invite_link(target)
                    except:
                        url = f"https://t.me/{BOT_USERNAME}"
                missing.append({"id": ch_id, "name": chat.title or "আমাদের চ্যানেল", "url": url})
        except Exception:
            pass
    return missing

# ---------------- HELPER: DELIVER VIDEO OR FORCE JOIN IN BOT DM ----------------
async def deliver_or_force_join(uid: int, vid: dict, part: dict, cfg: dict):
    vid_id = str(vid["_id"])
    missing = await get_missing_channels(uid, cfg)
    if missing:
        btns = [[InlineKeyboardButton(text=f"🔵 {ch['name']} (Join)", url=ch["url"])] for ch in missing]
        btns.append([InlineKeyboardButton(text="🟢 ✅ জয়েন করেছি — ভিডিও নিন", callback_data=f"verify_fj_{vid_id}_{part['part_num']}")])
        kb = InlineKeyboardMarkup(inline_keyboard=btns)
        cap = (
            f"🎬 <b>আপনার ভিডিওটি আনলক হয়েছে!</b>\n"
            f"🎞️ <b>{vid['title']} - {part['title']}</b>\n\n"
            f"🔒 ভিডিওটি পেতে নিচের চ্যানেলে জয়েন করে <b>জয়েন করেছি</b> বাটনে ক্লিক করুন 👇"
        )
        if vid.get("thumbnail"):
            sent = await bot.send_photo(uid, photo=vid["thumbnail"], caption=cap, parse_mode="HTML", reply_markup=kb)
        else:
            sent = await bot.send_message(uid, text=cap, parse_mode="HTML", reply_markup=kb)
        await log_bot_msg(uid, sent.message_id)
        return

    del_hours = cfg.get("auto_delete_hours", 6)
    warn_txt = f"\n\n⚠️ <i>সতর্কতা: এই ভিডিওটি আগামী {del_hours} ঘণ্টা পর অটোমেটিক ডিলিট হয়ে যাবে। তার আগেই দেখে নিন!</i>" if del_hours > 0 else ""
    total_parts = len(vid.get("parts", []))
    dm_btns = [[InlineKeyboardButton(text="🟢 আরও ভিডিও দেখুন", web_app=WebAppInfo(url=MINI_APP_URL))]]
    if part["part_num"] < total_parts:
        dm_btns.append([InlineKeyboardButton(text=f"🟡 পরের পার্ট (Part {part['part_num'] + 1:02d})", web_app=WebAppInfo(url=f"{MINI_APP_URL}?vid={vid_id}"))])

    sent = await bot.copy_message(
        chat_id=uid,
        from_chat_id=STORAGE_CHANNEL_ID,
        message_id=part["message_id"],
        caption=f"🎬 <b>{vid['title']}</b>\n🎞️ <b>{part['title']}</b>{warn_txt}",
        parse_mode="HTML",
        protect_content=vid.get("protect_content", True),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=dm_btns)
    )
    await log_bot_msg(uid, sent.message_id)

# ---------------- HELPER: 4-BUTTON START KEYBOARD ----------------
def build_start_keyboard(cfg: dict, vid_id: str = None) -> InlineKeyboardMarkup:
    app_url = f"{MINI_APP_URL}?vid={vid_id}" if vid_id else MINI_APP_URL
    rows = [[
        InlineKeyboardButton(text=cfg.get("watch_btn_text", "🟢 Watch Now"), web_app=WebAppInfo(url=app_url))
    ]]
    row2 = []
    if cfg.get("main_channel_url"):
        row2.append(InlineKeyboardButton(text=cfg.get("main_channel_name", "🔵 মেইন চ্যানেল"), url=cfg["main_channel_url"]))
    if cfg.get("request_channel_url"):
        row2.append(InlineKeyboardButton(text=cfg.get("request_channel_name", "🟡 রিকোয়েস্ট চ্যানেল"), url=cfg["request_channel_url"]))
    if row2:
        rows.append(row2)
    rows.append([
        InlineKeyboardButton(text="🎁 রেফারেল করুন (কয়েন আয়)", callback_data="my_ref")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------------- TELEGRAM BOT HANDLERS ----------------
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    args = message.text.split()
    payload = args[1] if len(args) > 1 else ""

    ref_by = None
    if payload.startswith("ref_"):
        try:
            r_id = int(payload.replace("ref_", ""))
            if r_id != uid:
                ref_by = r_id
        except:
            pass

    cfg = await get_settings()
    existing = await users_col.find_one({"user_id": uid})
    now = datetime.now(timezone.utc)
    if not existing:
        await users_col.insert_one({
            "user_id": uid,
            "name": message.from_user.full_name,
            "username": message.from_user.username or "",
            "coins": 0,
            "vip_until": None,
            "referred_by": ref_by,
            "last_checkin": None,
            "last_active": now,
            "created_at": now
        })
        if ref_by:
            await users_col.update_one({"user_id": ref_by}, {"$inc": {"coins": cfg.get("referral_coin_reward", 20)}})
    else:
        await users_col.update_one({"user_id": uid}, {"$set": {"last_active": now, "name": message.from_user.full_name}})

    if payload.startswith("vid_"):
        vid_id = payload.replace("vid_", "")
        if ObjectId.is_valid(vid_id):
            vid = await videos_col.find_one({"_id": ObjectId(vid_id)})
            if vid:
                kb = build_start_keyboard(cfg, vid_id=vid_id)
                cap = (
                    f"🎬 <b>{vid['title']}</b>\n"
                    f"🎞️ <b>মোট পার্ট:</b> {len(vid.get('parts', [])):02d}\n\n"
                    f"👇 নিচের <b>Watch Now</b> বাটনে ক্লিক করে ভিডিওটি দেখুন ও ডাউনলোড করুন:"
                )
                if vid.get("thumbnail"):
                    sent = await message.answer_photo(vid["thumbnail"], caption=cap, parse_mode="HTML", reply_markup=kb)
                else:
                    sent = await message.answer(cap, parse_mode="HTML", reply_markup=kb)
                await log_bot_msg(uid, sent.message_id)
                return

    app_name = cfg.get("app_name", "VideoVault")
    kb = build_start_keyboard(cfg)
    sent = await message.answer(
        f"👋 স্বাগতম <b>{message.from_user.first_name}</b>!\n\n"
        f"✨ <b>{app_name}</b>-এ আপনাকে স্বাগতম। নিচের বাটনে ক্লিক করে আপনার পছন্দের সব ভিডিও দেখুন 👇",
        parse_mode="HTML", reply_markup=kb
    )
    await log_bot_msg(uid, sent.message_id)

@dp.callback_query(F.data.startswith("verify_fj_"))
async def cb_verify_fj(call: types.CallbackQuery):
    parts_data = call.data.split("_")
    vid_id, part_num = parts_data[2], int(parts_data[3])
    uid = call.from_user.id
    cfg = await get_settings()
    missing = await get_missing_channels(uid, cfg)
    if missing:
        await call.answer("⚠️ আপনি এখনো চ্যানেলে জয়েন করেননি! আগে জয়েন করুন।", show_alert=True)
        return
    vid = await videos_col.find_one({"_id": ObjectId(vid_id)})
    part = next((p for p in vid.get("parts", []) if p["part_num"] == part_num), None) if vid else None
    if not vid or not part:
        await call.answer("ভিডিও পাওয়া যায়নি!", show_alert=True)
        return
    try:
        await call.message.delete()
    except:
        pass
    await deliver_or_force_join(uid, vid, part, cfg)
    await call.answer("✅ ভেরিফাই সফল!")

@dp.callback_query(F.data == "my_ref")
async def cb_my_ref(call: types.CallbackQuery):
    uid = call.from_user.id
    cfg = await get_settings()
    reward = cfg.get("referral_coin_reward", 20)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote('🔥 দারুণ সব প্রিমিয়াম ভিডিও দেখতে এখনি জয়েন করুন!')}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 বন্ধুদের শেয়ার করুন", url=share_url)]])
    sent = await call.message.answer(
        f"🎁 <b>আপনার রেফারেল লিংক:</b>\n<code>{ref_link}</code>\n\n"
        f"প্রতিটি সফল রেফারে আপনি পাবেন <b>+{reward} কয়েন</b>! কয়েন দিয়ে VIP কিনে বিজ্ঞাপন ছাড়াই ভিডিও দেখতে পারবেন।",
        parse_mode="HTML", reply_markup=kb
    )
    await log_bot_msg(uid, sent.message_id)
    await call.answer()

# Zero-Command Admin Video Upload
@dp.message(F.video | F.document)
async def handle_admin_video(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        try: await message.delete()
        except: pass
        return

    media = message.video or message.document
    if message.document and not (message.document.mime_type or "").startswith("video"):
        return

    copied = await bot.copy_message(chat_id=STORAGE_CHANNEL_ID, from_chat_id=message.chat.id, message_id=message.message_id)
    part_data = {
        "file_id": media.file_id,
        "message_id": copied.message_id,
        "file_size": round((media.file_size or 0) / (1024 * 1024), 2),
        "duration": getattr(media, "duration", 0)
    }

    current_state = await state.get_state()
    if current_state == UploadState.batch_parts.state:
        data = await state.get_data()
        vid_id = ObjectId(data["target_vid_id"])
        vid = await videos_col.find_one({"_id": vid_id})
        next_num = len(vid.get("parts", [])) + 1
        part_data["part_num"] = next_num
        part_data["title"] = f"Part {next_num:02d}"
        await videos_col.update_one({"_id": vid_id}, {"$push": {"parts": part_data}})
        clear_feed_cache()
        await message.reply(f"✅ <b>Part {next_num:02d}</b> যুক্ত হয়েছে! আরও ভিডিও পাঠান অথবা নিচের Done বাটনে চাপুন।", parse_mode="HTML")
        return

    await state.update_data(first_part=part_data)
    await state.set_state(UploadState.waiting_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🟢 নতুন ভিডিও (New)", callback_data="up_new"),
        InlineKeyboardButton(text="🟡 আগের ভিডিও (Add Parts)", callback_data="up_old")
    ]])
    await message.reply("🎬 ভিডিওটি পাওয়া গেছে! এটি কি নতুন ভিডিও নাকি আগের ভিডিওর পার্ট?", reply_markup=kb)

@dp.callback_query(F.data == "up_new")
async def cb_up_new(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(UploadState.waiting_thumb)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🟡 Skip Thumbnail", callback_data="skip_thumb")]])
    await call.message.edit_text("🖼️ এখন ভিডিওটির <b>Thumbnail ছবি</b> পাঠান (যে কোনো সাইজের ছবি অটোমেটিক 16:9 Blur হয়ে যাবে):", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "skip_thumb")
async def cb_skip_thumb(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(thumbnail="")
    await state.set_state(UploadState.waiting_title)
    await call.message.edit_text("✍️ এখন ভিডিওটির <b>Title</b> লিখুন:", parse_mode="HTML")

@dp.message(UploadState.waiting_thumb, F.photo)
async def handle_thumb(message: types.Message, state: FSMContext):
    wait_msg = await message.reply("⏳ Thumbnail প্রসেসিং ও ImgBB-তে আপলোড হচ্ছে...")
    thumb_url = await process_and_upload_thumb(message.photo[-1].file_id)
    await state.update_data(thumbnail=thumb_url)
    await state.set_state(UploadState.waiting_title)
    await wait_msg.edit_text("✅ Thumbnail সেভ হয়েছে!\n\n✍️ এখন ভিডিওটির <b>Title</b> লিখুন:", parse_mode="HTML")

@dp.message(UploadState.waiting_title, F.text)
async def handle_title(message: types.Message, state: FSMContext):
    data = await state.get_data()
    part = data["first_part"]
    part["part_num"] = 1
    part["title"] = "Part 01"
    doc = {
        "title": message.text.strip(),
        "thumbnail": data.get("thumbnail", ""),
        "parts": [part],
        "protect_content": True,
        "views": 0,
        "created_at": datetime.now(timezone.utc)
    }
    res = await videos_col.insert_one(doc)
    doc["_id"] = res.inserted_id
    clear_feed_cache()
    await state.update_data(target_vid_id=str(res.inserted_id))
    await state.set_state(UploadState.batch_parts)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🟢 আপলোড সম্পন্ন ও পাবলিশ করুন", callback_data="finish_upload")
    ]])
    await message.reply(
        f"✅ <b>{doc['title']}</b> সেভ হয়েছে!\n\nএখন যদি আরও পার্ট (৫০-১০০টি) থাকে তবে একসাথে ফরওয়ার্ড করুন, নাহলে নিচের বাটনে ক্লিক করে পাবলিশ করুন 👇",
        parse_mode="HTML", reply_markup=kb
    )

@dp.callback_query(F.data == "up_old")
async def cb_up_old(call: types.CallbackQuery, state: FSMContext):
    recent = await videos_col.find().sort("created_at", -1).limit(8).to_list(8)
    if not recent:
        await call.answer("কোনো আগের ভিডিও নেই!", show_alert=True)
        return
    btns = [[InlineKeyboardButton(text=f"🟡 {v['title'][:30]} ({len(v.get('parts', []))} Parts)", callback_data=f"selvid_{str(v['_id'])}")] for v in recent]
    await state.set_state(UploadState.waiting_existing_select)
    await call.message.edit_text("📂 কোন ভিডিওতে পার্টটি যোগ করবেন? সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("selvid_"))
async def cb_sel_vid(call: types.CallbackQuery, state: FSMContext):
    vid_id = call.data.replace("selvid_", "")
    data = await state.get_data()
    part = data["first_part"]
    vid = await videos_col.find_one({"_id": ObjectId(vid_id)})
    next_num = len(vid.get("parts", [])) + 1
    part["part_num"] = next_num
    part["title"] = f"Part {next_num:02d}"
    await videos_col.update_one({"_id": ObjectId(vid_id)}, {"$push": {"parts": part}})
    clear_feed_cache()
    await state.update_data(target_vid_id=vid_id)
    await state.set_state(UploadState.batch_parts)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🟢 আপলোড সম্পন্ন", callback_data="finish_upload_silent")
    ]])
    await call.message.edit_text(
        f"✅ <b>{vid['title']}</b>-এ <b>Part {next_num:02d}</b> যুক্ত হয়েছে!\nআরও পার্ট থাকলে পাঠান, শেষ হলে নিচের বাটনে চাপুন:",
        parse_mode="HTML", reply_markup=kb
    )

@dp.callback_query(F.data.in_(["finish_upload", "finish_upload_silent"]))
async def cb_finish_upload(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    vid_id = data.get("target_vid_id")
    await state.clear()
    if call.data == "finish_upload" and vid_id:
        vid = await videos_col.find_one({"_id": ObjectId(vid_id)})
        if vid:
            await send_channel_notification(vid)
    await call.message.edit_text("🎉 সম্পন্ন হয়েছে! ভিডিও এবং নোটিফিকেশন লাইভ হয়ে গেছে।")

@dp.message()
async def cleanup_spam(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        return
    try:
        await message.delete()
    except:
        pass

# ---------------- BACKGROUND WORKERS ----------------
async def auto_delete_worker():
    while True:
        try:
            now = datetime.now(timezone.utc)
            expired = await bot_msgs_col.find({"delete_at": {"$lte": now}}).limit(50).to_list(50)
            for item in expired:
                try:
                    await bot.delete_message(item["chat_id"], item["message_id"])
                except:
                    pass
                await bot_msgs_col.delete_one({"_id": item["_id"]})
        except Exception as e:
            print("AutoDelete Error:", e)
        await asyncio.sleep(60)

async def keep_alive_worker():
    await asyncio.sleep(30)
    while True:
        if MINI_APP_URL:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.get(f"{MINI_APP_URL}/health")
            except Exception:
                pass
        await asyncio.sleep(240)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    poll_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    clean_task = asyncio.create_task(auto_delete_worker())
    ping_task = asyncio.create_task(keep_alive_worker())
    yield
    poll_task.cancel()
    clean_task.cancel()
    ping_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def verify_telegram_init_data(init_data: str) -> dict:
    parsed = dict(urllib.parse.parse_qsl(init_data))
    received_hash = parsed.pop("hash", "")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram Auth")
    return json.loads(parsed["user"])

def verify_admin(x_admin_pass: str = Header(None)):
    if x_admin_pass != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Unauthorized Admin")

@app.get("/", response_class=HTMLResponse)
async def serve_mini_app():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/admin", response_class=HTMLResponse)
async def serve_admin_panel():
    with open("admin.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
async def health():
    return {"status": "ok"}

# ---------------- USER MINI APP APIS ----------------
@app.post("/api/auth")
async def api_auth(request: Request):
    body = await request.json()
    tg_user = verify_telegram_init_data(body.get("initData", ""))
    uid = tg_user["id"]
    now = datetime.now(timezone.utc)
    user = await users_col.find_one({"user_id": uid})
    if not user:
        user = {
            "user_id": uid, "name": tg_user.get("first_name", "User"),
            "username": tg_user.get("username", ""), "coins": 0,
            "vip_until": None, "last_checkin": None, "last_active": now, "created_at": now
        }
        await users_col.insert_one(user)
    else:
        await users_col.update_one({"user_id": uid}, {"$set": {"last_active": now}})

    cfg = await get_settings()
    is_vip = bool(user.get("vip_until") and user["vip_until"].replace(tzinfo=timezone.utc) > now)
    return {
        "user": {
            "id": uid, "name": user["name"], "coins": user.get("coins", 0),
            "is_vip": is_vip, "vip_until": user["vip_until"].isoformat() if is_vip else None,
            "ref_link": f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
        },
        "config": {
            "app_name": cfg.get("app_name", "VideoVault"),
            "welcome_notice": cfg.get("welcome_notice", "✨ প্রিমিয়াম ভিডিও লাইব্রেরিতে আপনাকে স্বাগতম!"),
            "monetag_zone_id": cfg.get("monetag_zone_id", ""),
            "has_direct_links": len(cfg.get("direct_links", [])) > 0,
            "ad_timer": cfg.get("ad_timer", 15),
            "vip_coin_price": cfg.get("vip_coin_price", 50),
            "daily_coin_reward": cfg.get("daily_coin_reward", 10)
        }
    }

@app.get("/api/videos")
async def api_videos(q: str = "", page: int = 1):
    if page == 1 and not q and FEED_CACHE["data"] and time.time() < FEED_CACHE["expires"]:
        return FEED_CACHE["data"]

    skip = (page - 1) * 16
    query = {"title": {"$regex": q, "$options": "i"}} if q else {}
    latest = await videos_col.find(query).sort("created_at", -1).skip(skip).limit(16).to_list(16)

    trending = []
    if page == 1 and not q:
        pipeline = [
            {"$group": {"_id": "$video_id", "score": {"$sum": 1}}},
            {"$sort": {"score": -1}},
            {"$limit": 6}
        ]
        top_ids = await trending_col.aggregate(pipeline).to_list(6)
        obj_ids = [ObjectId(x["_id"]) for x in top_ids if ObjectId.is_valid(x["_id"])]
        if obj_ids:
            t_docs = await videos_col.find({"_id": {"$in": obj_ids}}).to_list(6)
            t_map = {str(d["_id"]): d for d in t_docs}
            trending = [t_map[x["_id"]] for x in top_ids if x["_id"] in t_map]

    def serialize(v):
        return {
            "id": str(v["_id"]), "title": v["title"], "thumbnail": v.get("thumbnail", ""),
            "parts_count": len(v.get("parts", [])), "views": v.get("views", 0),
            "created_at": v["created_at"].strftime("%d %b %Y")
        }

    result = {"trending": [serialize(v) for v in trending], "latest": [serialize(v) for v in latest]}
    if page == 1 and not q:
        FEED_CACHE["data"] = result
        FEED_CACHE["expires"] = time.time() + 30
    return result

@app.get("/api/video/{vid_id}")
async def api_video_detail(vid_id: str, x_init_data: str = Header("")):
    tg_user = verify_telegram_init_data(x_init_data)
    uid = tg_user["id"]
    vid = await videos_col.find_one({"_id": ObjectId(vid_id)})
    if not vid:
        raise HTTPException(404, "Video not found")
    user_unlocks = await unlocks_col.find({"user_id": uid, "video_id": vid_id}).to_list(200)
    unlocked_parts = {u["part_num"] for u in user_unlocks}
    parts = [{"part_num": p["part_num"], "title": p["title"], "size": p.get("file_size", 0), "unlocked": p["part_num"] in unlocked_parts} for p in vid.get("parts", [])]
    return {"id": str(vid["_id"]), "title": vid["title"], "thumbnail": vid.get("thumbnail", ""), "views": vid.get("views", 0), "parts": parts}

@app.post("/api/unlock/direct-link")
async def api_get_direct_link(request: Request):
    body = await request.json()
    tg_user = verify_telegram_init_data(body.get("initData", ""))
    cfg = await get_settings()
    links = cfg.get("direct_links", [])
    if not links:
        raise HTTPException(400, "No direct links configured")
    AD_SESSIONS[tg_user["id"]] = time.time()
    return {"url": random.choice(links), "timer": cfg.get("ad_timer", 15)}

@app.post("/api/unlock/complete")
async def api_unlock_complete(request: Request):
    body = await request.json()
    tg_user = verify_telegram_init_data(body.get("initData", ""))
    uid = tg_user["id"]
    vid_id = body.get("video_id")
    part_num = int(body.get("part_num"))
    method = body.get("method", "ad")

    vid = await videos_col.find_one({"_id": ObjectId(vid_id)})
    if not vid:
        raise HTTPException(404, "Video not found")
    part = next((p for p in vid.get("parts", []) if p["part_num"] == part_num), None)
    if not part:
        raise HTTPException(404, "Part not found")

    now = datetime.now(timezone.utc)
    user = await users_col.find_one({"user_id": uid})
    cfg = await get_settings()
    is_vip = bool(user.get("vip_until") and user["vip_until"].replace(tzinfo=timezone.utc) > now)

    already = await unlocks_col.find_one({"user_id": uid, "video_id": vid_id, "part_num": part_num})
    if not already and not is_vip:
        if method == "direct":
            req_sec = cfg.get("ad_timer", 15)
            started = AD_SESSIONS.get(uid, 0)
            if (time.time() - started) < max(1, req_sec - 1):
                raise HTTPException(400, f"⚠️ আপনি বিজ্ঞাপনটি {req_sec} সেকেন্ড দেখেননি! পুনরায় চেষ্টা করুন।")
            AD_SESSIONS.pop(uid, None)
        elif method == "coin":
            if user.get("coins", 0) < 10:
                raise HTTPException(400, "পর্যাপ্ত কয়েন নেই (১০ কয়েন প্রয়োজন)")
            await users_col.update_one({"user_id": uid}, {"$inc": {"coins": -10}})
        exp_hours = cfg.get("unlock_expiry_hours", 24)
        await unlocks_col.insert_one({
            "user_id": uid, "video_id": vid_id, "part_num": part_num,
            "expires_at": now + timedelta(hours=exp_hours)
        })

    await videos_col.update_one({"_id": ObjectId(vid_id)}, {"$inc": {"views": 1}})
    await trending_col.insert_one({"video_id": vid_id, "created_at": now})

    try:
        await deliver_or_force_join(uid, vid, part, cfg)
    except Exception:
        raise HTTPException(400, "প্রথমে বটে /start দিন যাতে বট আপনাকে মেসেজ পাঠাতে পারে!")

    return {"ok": True, "bot_url": f"https://t.me/{BOT_USERNAME}"}

@app.post("/api/checkin")
async def api_checkin(request: Request):
    body = await request.json()
    tg_user = verify_telegram_init_data(body.get("initData", ""))
    uid = tg_user["id"]
    user = await users_col.find_one({"user_id": uid})
    now = datetime.now(timezone.utc)
    if user.get("last_checkin") and (now - user["last_checkin"].replace(tzinfo=timezone.utc)).total_seconds() < 86400:
        raise HTTPException(400, "আজকের চেক-ইন ইতিমধ্যে নেওয়া হয়েছে!")
    reward = (await get_settings()).get("daily_coin_reward", 10)
    await users_col.update_one({"user_id": uid}, {"$inc": {"coins": reward}, "$set": {"last_checkin": now}})
    return {"coins": user.get("coins", 0) + reward, "reward": reward}

@app.post("/api/buy-vip")
async def api_buy_vip(request: Request):
    body = await request.json()
    tg_user = verify_telegram_init_data(body.get("initData", ""))
    uid = tg_user["id"]
    cfg = await get_settings()
    price = cfg.get("vip_coin_price", 50)
    user = await users_col.find_one({"user_id": uid})
    if user.get("coins", 0) < price:
        raise HTTPException(400, f"VIP কিনতে {price} কয়েন প্রয়োজন!")
    now = datetime.now(timezone.utc)
    base = user["vip_until"].replace(tzinfo=timezone.utc) if (user.get("vip_until") and user["vip_until"].replace(tzinfo=timezone.utc) > now) else now
    new_vip = base + timedelta(days=1)
    await users_col.update_one({"user_id": uid}, {"$inc": {"coins": -price}, "$set": {"vip_until": new_vip}})
    return {"vip_until": new_vip.isoformat(), "coins": user.get("coins", 0) - price}

# ---------------- WEB ADMIN PANEL APIS ----------------
@app.get("/api/admin/stats")
async def admin_stats(x_admin_pass: str = Header(None)):
    verify_admin(x_admin_pass)
    now = datetime.now(timezone.utc)
    total_users = await users_col.count_documents({})
    live_online = await users_col.count_documents({"last_active": {"$gte": now - timedelta(minutes=5)}})
    active_24h = await users_col.count_documents({"last_active": {"$gte": now - timedelta(hours=24)}})
    total_videos = await videos_col.count_documents({})
    trending_24h = await trending_col.count_documents({})
    return {
        "total_users": total_users,
        "live_online": live_online,
        "active_24h": active_24h,
        "total_videos": total_videos,
        "unlocks_24h": trending_24h
    }

@app.get("/api/admin/settings")
async def admin_get_settings(x_admin_pass: str = Header(None)):
    verify_admin(x_admin_pass)
    return await get_settings()

@app.post("/api/admin/settings")
async def admin_save_settings(request: Request, x_admin_pass: str = Header(None)):
    verify_admin(x_admin_pass)
    data = await request.json()
    data.pop("_id", None)
    await settings_col.update_one({"_id": "global"}, {"$set": data})
    clear_feed_cache()
    return {"ok": True}

@app.get("/api/admin/videos")
async def admin_list_videos(x_admin_pass: str = Header(None)):
    verify_admin(x_admin_pass)
    vids = await videos_col.find().sort("created_at", -1).limit(50).to_list(50)
    return [{"id": str(v["_id"]), "title": v["title"], "parts": len(v.get("parts", [])), "views": v.get("views", 0), "protect": v.get("protect_content", True)} for v in vids]

@app.delete("/api/admin/video/{vid_id}")
async def admin_delete_video(vid_id: str, x_admin_pass: str = Header(None)):
    verify_admin(x_admin_pass)
    await videos_col.delete_one({"_id": ObjectId(vid_id)})
    clear_feed_cache()
    return {"ok": True}

@app.patch("/api/admin/video/{vid_id}/protect")
async def admin_toggle_protect(vid_id: str, x_admin_pass: str = Header(None)):
    verify_admin(x_admin_pass)
    vid = await videos_col.find_one({"_id": ObjectId(vid_id)})
    new_val = not vid.get("protect_content", True)
    await videos_col.update_one({"_id": ObjectId(vid_id)}, {"$set": {"protect_content": new_val}})
    return {"protect": new_val}

async def do_broadcast(text: str, photo_url: str):
    cursor = users_col.find({}, {"user_id": 1})
    async for u in cursor:
        try:
            if photo_url:
                sent = await bot.send_photo(u["user_id"], photo=photo_url, caption=text, parse_mode="HTML")
            else:
                sent = await bot.send_message(u["user_id"], text=text, parse_mode="HTML")
            await log_bot_msg(u["user_id"], sent.message_id)
            await asyncio.sleep(0.05)
        except:
            pass

@app.post("/api/admin/broadcast")
async def admin_broadcast(request: Request, bg: BackgroundTasks, x_admin_pass: str = Header(None)):
    verify_admin(x_admin_pass)
    body = await request.json()
    bg.add_task(do_broadcast, body.get("text", ""), body.get("photo", ""))
    return {"ok": True, "message": "Broadcast started in background"}
