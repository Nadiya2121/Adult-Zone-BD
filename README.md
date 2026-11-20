এই নাও তোমার প্রজেক্টের জন্য **পাই টু পাই গোছানো সম্পূর্ণ `README.md`**। এটি তোমার GitHub রিপোজিটরিতে সেভ করে রাখলে ভবিষ্যতে তুমি বা যেকোনো ডেভেলপার এক নজরেই পুরো সিস্টেম এবং প্রতিটি ফাইলের কাজ বুঝে যাবে।

### `README.md`
```markdown
# 🎬 VideoVault — Premium Telegram Video Mini App Platform

**VideoVault** হলো একটি আধুনিক, দ্রুতগতির এবং সম্পূর্ণ স্বয়ংক্রিয় (Automated) Telegram Video Mini App প্ল্যাটফর্ম। এখানে ইউজাররা Telegram Mini App-এর মাধ্যমে প্রিমিয়াম ক্যাটালগ দেখতে ও বিজ্ঞাপন/কয়েন দিয়ে ভিডিও আনলক করতে পারে এবং বটের ইনবক্সে (DM) সুরক্ষিতভাবে ভিডিও ফাইল পেয়ে যায়।

---

## 📁 প্রজেক্ট ফাইল স্ট্রাকচার ও প্রতিটি ফাইলের কাজ

```text
VideoVault/
├── requirements.txt   # প্রজেক্টের প্রয়োজনীয় Python লাইব্রেরি তালিকা
├── .env               # গোপন টোকেন, পাসওয়ার্ড ও সার্ভার কনফিগারেশন
├── db.py              # MongoDB ডাটাবেস কানেকশন, ইনডেক্স ও ডিফল্ট সেটিংস
├── main.py            # মূল সার্ভার (FastAPI API + Aiogram Bot + ব্যাকগ্রাউন্ড ওয়ার্কার)
├── index.html         # ইউজারদের জন্য Telegram Mini App ফ্রন্টএন্ড (UI/UX)
├── admin.html         # অ্যাডমিনদের জন্য Web Admin Dashboard (/admin)
└── README.md          # প্রজেক্ট ডকুমেন্টেশন ও গাইডলাইন
```

### ফাইলগুলোর বিস্তারিত বিবরণ:
1. **`requirements.txt`**: FastAPI, Uvicorn, Aiogram 3, Motor (Async MongoDB), Pillow (ইমেজ প্রসেসিং), এবং HTTPX লাইব্রেরিগুলোর তালিকা।
2. **`db.py`**:
   * MongoDB Atlas-এর সাথে Async কানেকশন তৈরি করে।
   * `users`, `videos`, `unlocks`, `trending_logs`, `bot_messages`, এবং `settings` কালেকশন পরিচালনা করে।
   * **Auto-Expiry (TTL Index):** ২৪ ঘণ্টা পর `trending_logs` এবং নির্ধারিত সময় পর `unlocks` অটোমেটিক ডাটাবেস থেকে মুছে দেয়।
3. **`main.py`**:
   * **Telegram Bot (Aiogram 3):** জিরো-কমান্ড ভিডিও আপলোড (`UPLOAD_LOCK` সহ যাতে একসাথে অনেক পার্ট পাঠালেও সিরিয়াল ঠিক থাকে), ৪ বাটনের `/start` মেনু, Bot DM Force Join ভেরিফিকেশন, এবং স্প্যাম ক্লিনআপ।
   * **Image Processor:** যেকোনো সাইজের থাম্বনেইল না কেটে (No Crop) পাশে সুন্দর Blur করে 16:9 YouTube সাইজে রূপান্তর করে **ImgBB API**-তে আপলোড করে।
   * **FastAPI Backend:** Mini App ও Admin Panel-এর সব API পরিচালনা করে, Telegram `initData` (HMAC-SHA256) ভেরিফাই করে, এবং হোম ফিডের জন্য **৩০ সেকেন্ডের Smart RAM Cache** চালায়।
   * **Background Workers:** মেয়াদ শেষ হওয়া বট মেসেজ অটো-ডিলিট (`auto_delete_worker`) এবং ফ্রি সার্ভার সচল রাখার জন্য প্রতি ৪ মিনিট পরপর **Keep-Alive Self Ping** (`keep_alive_worker`) চালায়।
4. **`index.html`**:
   * Tailwind CSS দিয়ে তৈরি ১-কলামের Cinematic Video Card, Skeleton Shimmer Loading, ও অটো-স্ক্রলিং ২৪ ঘণ্টার টপ ট্রেন্ডিং।
   * ৫ বাটনের Glassmorphism Bottom Navigation Bar (`হোম`, `টপ`, `সার্চ`, `সেভড`, `প্রোফাইল`)।
   * **১-ধাপ ব্যাক বাটন ফিক্স:** মোবাইল ও টেলিগ্রামের ব্যাক বাটনে ক্লিক করলে অ্যাপ বন্ধ না হয়ে এক ধাপ পেছনে ফিরে আসে।
   * **Ad Anti-Cheat:** Direct Link-এ ক্লিক করে নির্ধারিত সময়ের আগে ফিরে আসলে ভিডিও আনলক হয় না।
   * **Save / Unsave (Bookmark):** `localStorage` ব্যবহার করে দ্রুত সেভ/আনসেভ সুবিধা।
5. **`admin.html`**:
   * `/admin` রুটে পাসওয়ার্ড-সুরক্ষিত ওয়েব ড্যাশবোর্ড।
   * **লাইভ স্ট্যাটাস:** Live Online (গত ৫ মিনিট), মোট ইউজার, ২৪ ঘণ্টার অ্যাক্টিভ ইউজার, মোট ভিডিও ও ২৪ ঘণ্টার আনলক।
   * **Broadcast:** সকল ইউজারকে ছবি ও টেক্সট ব্রডকাস্ট (অটো-ডিলিট সাপোর্টেড)।
   * **Dynamic Settings:** App Name, Welcome Notice, Monetag Zone ID, Unlimited Random Direct Links, Ad Timer, VIP Coin Price, Auto-Delete Hours, Notification Channel ও বাটন টেক্সট/লিংক, এবং শুধু Channel ID দিয়ে Force Join নিয়ন্ত্রণ।

---

## ⚙️ পুরো সিস্টেম যেভাবে কাজ করে (Workflow)

### ১. অ্যাডমিন ভিডিও আপলোড (Zero-Command)
1. অ্যাডমিন বটের ইনবক্সে সরাসরি ভিডিও ফরওয়ার্ড বা আপলোড করেন (কোনো কমান্ড ছাড়াই)।
2. ভিডিওটি স্বয়ংক্রিয়ভাবে `STORAGE_CHANNEL_ID`-তে সেভ হয়।
3. বট বাটন দেখায়: **`🟢 নতুন ভিডিও`** নাকি **`🟡 আগের ভিডিও (Add Parts)`**।
4. নতুন ভিডিও হলে ছবি (ImgBB-তে 16:9 Blur হয়ে সেভ হয়) এবং টাইটেল নেওয়ার পর একসাথে ৫০-১০০টি পার্ট ফরওয়ার্ড করা যায়।
5. পাবলিশ করার সাথে সাথেই **Notification Channel**-এ থাম্বনেইল, টাইটেল ও বাটনসহ পোস্ট চলে যায়।

### ২. ইউজার ভিডিও আনলক ও ডেলিভারি
1. ইউজার Mini App ওপেন করে ভিডিও সিলেক্ট করে এবং পার্টের **`🔒 Unlock`** বাটনে ক্লিক করে।
2. **Unlock অপশন:** Monetag Video Ad, Random Direct Link Ad (Anti-Cheat টাইমার সহ), অথবা কয়েন/VIP দিয়ে আনলক করতে পারে।
3. আনলক সফল হলে ইউজার বটের ইনবক্সে (DM) চলে যায়।
4. **Bot DM Force Join:** ইউজার প্রয়োজনীয় চ্যানেলে জয়েন না থাকলে বট নিজেই চ্যানেলের নাম ও লিংকসহ জয়েন বাটন দেখায়। জয়েন করে **`🟢 ✅ জয়েন করেছি — ভিডিও নিন`** বাটনে চাপলেই মেসেজ ডিলিট হয়ে ভিডিও ফাইল ডেলিভারি হয় (অটো-ডিলিট সতর্কতা ও পরের পার্টের বাটনসহ)।

---

## 🔑 Environment Variables (`.env` / Hosting)

| Variable | বিবরণ |
| :--- | :--- |
| `BOT_TOKEN` | @BotFather থেকে পাওয়া টেলিগ্রাম বট টোকেন |
| `ADMIN_IDS` | অ্যাডমিনদের Telegram User ID (কমা দিয়ে একাধিক দেওয়া যাবে) |
| `ADMIN_PASSWORD` | `/admin` ওয়েব প্যানেলে লগইন করার পাসওয়ার্ড |
| `MONGO_URI` | MongoDB Atlas-এর কানেকশন লিংক |
| `IMGBB_API_KEY` | `api.imgbb.com` থেকে পাওয়া ফ্রি API Key |
| `STORAGE_CHANNEL_ID` | প্রাইভেট স্টোরেজ চ্যানেল আইডি (`-100...`, বটকে Admin রাখতে হবে) |
| `MINI_APP_URL` | হোস্ট করা সার্ভারের লাইভ URL (যেমন: `https://app.onrender.com`) |
| `BOT_USERNAME` | বটের ইউজারনেম (`@` ছাড়া) |
| `PYTHON_VERSION` | `3.11.9` (Render-এ ডিপ্লয়ের জন্য প্রস্তাবিত) |

---

## 🚀 হোস্টিং ও রান করার নিয়ম (Render / Railway)

1. বটকে অবশ্যই **Storage Channel**, **Notification Channel**, এবং **Force Join Channel**-গুলোতে **Admin** বানাতে হবে।
2. **MongoDB Atlas**-এ **Network Access**-এ `0.0.0.0/0` (Allow from Anywhere) সেট করতে হবে।
3. Start Command:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
4. ডিপ্লয় হওয়ার পর `https://your-domain.com/admin` লিংকে গিয়ে `ADMIN_PASSWORD` দিয়ে লগইন করে চ্যানেল, বিজ্ঞাপন ও টাইমার সেট করে সেভ করুন।
```

এটি তোমার রিপোজিটরিতে **`README.md`** নামে সেভ করে রাখো—তাহলে প্রজেক্টটি একদম ১০০% প্রফেশনাল ও ডকুমেন্টেড হয়ে থাকবে!
