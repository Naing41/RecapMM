# RecapMM — Windows version (Server မလို)

ဗီဒီယိုတစ်ခုကို မြန်မာလို recap/dub လုပ်ပေးတဲ့ Windows app။ **VPS / Subscription Key မလိုပါ** — Internet ပဲလိုတယ်။

`ဗီဒီယို → Whisper (ဒီ PC ပေါ်) → Gemini ဘာသာပြန် (Google ကို တိုက်ရိုက်) → မြန်မာအသံ (Edge / Google) → ffmpeg Smart Sync → _Myanmar.mp4 + .srt`

## သုံးနည်း (exe)
1. GitHub repo **အသစ်** (Android repo နဲ့ မရောပါနဲ့) ထဲ ဒီ folder ထဲက ဖိုင်အကုန် (zip မဟုတ်ဘဲ) တင်ပါ။
2. *Actions → Build Windows app → Run workflow* (၁၀–၂၀ မိနစ်)။ ဘာ Secret/Variable မှ မလိုပါ။
3. Artifacts › `RecapMM-Windows` ကို ဒေါင်း → unzip → `RecapMM.exe` နှစ်ချက်နှိပ်။
4. **ဆက်တင်** tab မှာ Gemini API key ထည့်ပြီး သိမ်း → **ဖန်တီး** tab မှာ ဗီဒီယိုရွေး → ▶ စတင်မယ်။

UI က Liquid Glass (Android app နဲ့ တူ — လှုပ်ရှားနေတဲ့ နောက်ခံအရောင် + glass card)။ ဗီဒီယိုကို window ပေါ် drag လုပ်လည်းရ။ Actions မှာ `ui-preview` artifact နဲ့ screenshot ကြည့်လို့ရ။

Python ရှိရင် `run_from_source.bat` နဲ့လည်း တန်းဖွင့်လို့ရတယ် (ffmpeg ကို PATH ထဲ / ဒီ folder ထဲ ထားပါ)။

## ပထမဆုံးအကြိမ်
- Whisper model ကို အလိုလို ဒေါင်းမယ် (small ≈ 460 MB)၊ အင်တာနက်လိုတယ်။
- Gemini ကို ပိတ်ထားတဲ့ နေရာဆိုရင် PC မှာ VPN ဖွင့်ပါ။
- ရပ်လိုက်ရင် ပြီးသမျှ သိမ်းထားတယ် — ဗီဒီယိုတူကို ပြန်စရင် ရပ်ခဲ့တဲ့နေရာကနေ ဆက်မယ် (`%LOCALAPPDATA%\RecapMM\jobs`)။

## Tools
| Tool | အခြေအနေ |
|---|---|
| Recap | ✅ |
| SRT (စာတန်းဖိုင်ထုတ်၊ မြန်မာလိုပြန်/မူရင်း) | ✅ |
| TTS (စာ → အသံ .mp3) | ✅ |
| Splitter (re-encode မလုပ်ဘဲ အပိုင်းလိုက်ဖြတ်) | ✅ |
| Dhamma Studio (clip များ + တရားအသံ၊ ticker / ခေါင်းစဉ် / intro-outro / logo) | ✅ |
| Voice Clone (ကိုယ်ပိုင်အသံ — အခမဲ့ Colab GPU) | ✅ |
| Dubbing Studio (ကိုယ်ပိုင် voice ဖိုင်နဲ့ ရုပ်ကို ချိန်ညှိ — TTS/Gemini မလို) | ✅ |
| Movie Recap (ရုပ်ရှင်အပြည့် → key point → မြန်မာ narration + အသံ + ရုပ်) | ✅ |
| Subtitle Only (အပိုင်းအများကြီး / folder တစ်ခုလုံး → စကားပြောချိန်ပဲ မြန်မာစာတန်းထိုး + blur) | ✅ |
| YouTube Link | Coming Soon (server လို) |

### Subtitle Only
1. ဗီဒီယိုတွေကို **＋ ဖိုင်များ** / **📂 Folder** နဲ့ ထည့် (သို့) window ပေါ် drag လုပ် — folder ထဲက subfolder အပါအဝင် အကုန်ယူ၊ EP1 → EP2 → EP10 အစီအစဉ်အတိုင်း စီပေးမယ် (၈၀ ပိုင်းလည်းရ)။
2. စာတန်း ပုံစံ ညှိ — Blur ဘားရဲ့ နေရာ/အမြင့် (မူရင်းစာတန်း ဖုံးအောင်)၊ စာအရွယ်၊ အရောင်၊ blur က စာတန်းပြတဲ့အချိန်ပဲလား တစ်ခုလုံးလား။
3. **စတင်မယ်** — တစ်ပိုင်းချင်း Whisper နဲ့ နားထောင် → Gemini မြန်မာလိုပြန် (ဇာတ်ကောင်နာမည်အတွက် ရှေ့အပိုင်းက မှတ်ဉာဏ်ကို ဆက်သုံး) → စကားပြောတဲ့ အချိန်မှာပဲ စာတန်းထိုး။ မူရင်းအသံ မပြောင်းပါ။
4. ရလဒ် `<out>\Subbed\EPxx_sub.mp4` + `EPxx_mm.srt` တစ်ပိုင်းချင်း။ တစ်ပိုင်း မအောင်ရင် ကျန်တာ ဆက်လုပ်၊ ရပ်ပြီးပြန်စရင် ပြီးသားကျော်။ "SRT ဖိုင်ပဲ" ရွေးရင် ဗီဒီယို ပြန်မ encode ဘူး (မြန်)။
- Gemini free quota နဲ့ ၈၀ ပိုင်းက ကြာမယ် — key အများကြီးထည့်ထားရင် ပြည့်ရင် နောက် key သုံးမယ်၊ အကုန်ပြည့်ရင် ရပ်ပြီး နောက်နေ့ ပြန်စလို့ရ။

### Dubbing Studio
မူရင်းဗီဒီယို + ကိုယ်ပိုင် မြန်မာ voice ဖိုင်ကို ရွေးပါ။ Voice ကို မဆွဲ မရွှေ့ဘဲ ရုပ်ကို voice ရဲ့ ရပ်နားချိန်တွေမှာ ဖြတ်ပြီး voice အရှည်နဲ့ ကိုက်အောင် ညှိမယ် (ရှည်ရင် freeze/slow၊ တိုရင် 1.25× အထိ မြန်)။ မူရင်းစကားကို Whisper က ဒီ PC မှာ နားထောင်တယ်။

### Movie Recap
1. ရုပ်ရှင် (၂ မိနစ်ထက်ရှည်) ရွေး၊ Subtitle .srt ရှိရင် ထည့် (မထည့်ရင် Whisper)၊ ad/credit ဖြတ်ချင်ရင် စ/ဆုံး အချိန်ထည့်။
2. **🔍 စိစစ်မယ်** — AI က ဇာတ်ကားတစ်ခုလုံး (စကားပြော + frame) ကြည့်ပြီး ဇာတ်လမ်း၊ ဇာတ်ကောင်၊ key point တွေ ထုတ်ပေးမယ်။ မလိုတာ ✓ ဖြုတ်။
3. အသံရွေး (Edge Thiha/Nilar · Google · 🧬 Voice Clone) → **🎬 Recap ထုတ်မယ်**။ ပြီးသမျှ သိမ်းထားလို့ ရပ်ပြီး ပြန်စရင် ဆက်မယ်။
4. ရလဒ် `_recap_<N>m.mp4` + narration `.srt`။
- အင်တာနက် လိုတယ် (Gemini + Edge/Google အသံ)။ Voice Clone ရွေးရင် Colab လည်းလို။ အင်တာနက်လုံးဝမလိုတဲ့ offline လုပ်ဖို့ မြန်မာဘာသာပြန်/အသံ local model မရှိလို့ မဖြစ်နိုင်ပါ — ဒါပေမယ့် Server (VPS) လုံးဝမလိုပါ။

### Voice Clone သုံးနည်း (Server မလို)
1. **Tools › Voice Clone › 📓 Notebook သိမ်း + Colab ဖွင့်** — notebook ကို `Downloads\RecapMM` ထဲ သိမ်းပြီး Colab ဖွင့်ပေးမယ်။
2. Colab မှာ *File › Upload notebook* → `RecapMM_Voice_Clone.ipynb`၊ *Runtime › Change runtime type › GPU (T4)*။
3. cell ကို ▶ နှိပ် (model ဒေါင်းလို့ ပထမတစ်ခါ ၅–၁၀ မိနစ်) — ပြီးရင် `RCP1:…` ကုဒ်ပေါ်လာမယ်၊ ကူးပါ။
4. App ကို ပြန်လာရင် Clipboard ထဲက ကုဒ်ကို အလိုလို ချိတ်မယ် (မချိတ်ရင် 📋 သို့ paste)။
5. **အသံနမူနာ** ထည့် (၁၀–၁၅ စက္ကန့်၊ ရှင်းတဲ့အသံ) — Gemini key ရှိရင် AI က စာသား ဖတ်ပေးမယ်၊ မှန်အောင် ပြင်ပါ (စာသားပါမှ အသံ တူတယ်)။
6. စာရိုက် → **စတင်မယ်** → `.mp3`။
- Colab ပြတ်ရင် (free က နာရီအနည်းငယ်) ကုဒ်အသစ် ပြန်ယူရမယ်။ GPU မရရင် CPU နဲ့ အရမ်းနှေးမယ်။
- နမူနာအသံကို ဖိုင်ကနေပဲ ထည့်လို့ရတယ် (microphone သွင်းတာ Windows version မှာ မပါသေး)။

## Android version နဲ့ ကွာတာ
- **ပါတယ်:** Whisper၊ Story-context + validated Gemini ဘာသာပြန်၊ Edge (Thiha/Nilar) + Google အသံ၊ Smart Sync (အသံအရှည်နဲ့ ရုပ်ကို ညှိ၊ 1.25× ထက် မမြန်)၊ SRT၊ resume။
- **မပါသေး:** Dubbing မှာ Gemini နဲ့ ကိုက်ညှိတာ (anchor)၊ Movie Recap မှာ subtitle burn-in၊ မိုက်နဲ့ အသံနမူနာ သွင်းတာ၊ subtitle burn-in၊ Gemini TTS၊ Gemini audit pass၊ ယောက်ျား/မိန်းမ အသံခွဲ။
- **Coming Soon:** YouTube link (server လိုတယ်)။

## သိထားရမယ့်အချက်
- Windows မှာ မစမ်းရသေးပါ (Linux မှာ pipeline ကို mock နဲ့ စမ်းထားတာ) — error ပေါ်ရင် log ပို့ပါ။
- NVIDIA GPU ရှိပြီး CUDA library တပ်ထားရင် Whisper က GPU သုံးမယ်၊ မရှိရင် CPU (int8)။ CPU မှာ `small` က ဗီဒီယိုအရှည်ရဲ့ ~၁–၂ ဆ ကြာနိုင်တယ်။
- Edge/Google အသံက တရားဝင် API မဟုတ်လို့ Microsoft/Google ပြောင်းရင် ရပ်နိုင်တယ်။
