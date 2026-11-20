import os
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db = client["videovault_db"]

users_col = db["users"]
videos_col = db["videos"]
unlocks_col = db["unlocks"]
trending_col = db["trending_logs"]
bot_msgs_col = db["bot_messages"]
settings_col = db["settings"]

async def init_db():
    # Indexes for ultra-fast queries on free servers
    await users_col.create_index("user_id", unique=True)
    await videos_col.create_index([("created_at", -1)])
    await videos_col.create_index([("title", "text")])
    await unlocks_col.create_index([("user_id", 1), ("video_id", 1), ("part_num", 1)])
    # Auto-expire unlocks based on expires_at
    await unlocks_col.create_index("expires_at", expireAfterSeconds=0)
    # Auto-clear 24h trending logs after 86400 seconds (24 Hours)
    await trending_col.create_index("created_at", expireAfterSeconds=86400)
    await bot_msgs_col.create_index("delete_at")

    # Default Settings if not exists
    if not await settings_col.find_one({"_id": "global"}):
        await settings_col.insert_one({
            "_id": "global",
            "app_name": "VideoVault",
            "monetag_zone_id": "",
            "direct_links": [],
            "ad_timer": 15,
            "unlock_expiry_hours": 24,
            "vip_coin_price": 50,
            "daily_coin_reward": 10,
            "referral_coin_reward": 20,
            "auto_delete_hours": 6,
            "force_channels": [],          # [{"id": "@channel", "name": "Main Channel", "url": "https://t.me/..."}]
            "notification_channel_id": "",
            "watch_btn_text": "🟢 Watch Now",
            "tutorial_btn_text": "🟡 কিভাবে ভিডিও দেখবেন?",
            "tutorial_btn_url": "",
            "custom_btn_text": "🔵 আমাদের মেইন চ্যানেলে জয়েন করুন",
            "custom_btn_url": ""
        })

async def get_settings():
    return await settings_col.find_one({"_id": "global"})
