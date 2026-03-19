# 🐷 Shiny Pig Profit Tracker

A lightweight local web app that tracks **profit from Year Of The Pig event** in Hypixel SkyBlock by reading your Minecraft Chat log file in real time.

---

## ✨ Features

- 📊 Live profit tracking (coins, items, net profit)
- 🛒 Bazaar price integration (auto-updated)
- 📜 Real-time event feed from Minecraft logs
- ⚙️ Configurable log path via UI
- 📈 Stats:
  - Total profit
  - Profit per pig
  - Profit per hour
  - Coins vs item value
- 🔍 Event filtering + clear history

<img width="1025" height="905" alt="image" src="https://github.com/user-attachments/assets/ca7a47f5-fac7-4ab2-aee2-79db5c64f13b" />

---

## 🚀 How It Works

1. Reads your `latest.log` file from Minecraft
2. Detects:
   - SHINY drops (coins, items, XP)
   - Bazaar buys/sells
3. Fetches live prices from Hypixel API
4. Calculates profit in real time
5. Displays everything in a local web dashboard

---

## 🚀 How To Start

1. Open Terminal in Folder where pigtracker.py is located.
2. Type python pigtracker.py inside Terminal to start.
3. If you play any other launcher than standard Minecraft Launcher, you will need to add the path to your latest.log file on the web page that opened up.
4. latest.log can be found inside Minecraft Folder (any launcher) /logs/latest.log most likely inside your appdata
5. Once you start catching the pigs, drops will display on the web page in real time.
6. For now drops like, Farming For Dummies, Potato Talisman, Harvesting VI and Blood God Crest have fixed values that can be changed inside the pigtracker.py code.

---
## 📦 Requirements

- Python **3.8+**
- Internet connection (for Hypixel API)
- Minecraft (for log file)

---


