import streamlit as st
import time
import json
import google.generativeai as genai
import yfinance as yf
import plotly.graph_objects as go
import os
import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

# ==========================================
# 1. 密碼大門邏輯 (Security Gate)
# ==========================================
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"] 
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.title("🔐 股票學術交流站 - 認證登入")
        st.text_input("請輸入通關密碼：", type="password", on_change=password_entered, key="password")
        st.info("本系統僅供學術交流使用，請向管理員索取密碼。")
        return False
    elif not st.session_state["password_correct"]:
        st.title("🔐 股票學術交流站 - 認證登入")
        st.text_input("請輸入通關密碼：", type="password", on_change=password_entered, key="password")
        st.error("❌ 密碼錯誤，請重新輸入。")
        return False
    return True

if not check_password():
    st.stop()

# ==========================================
# 2. 智能調度中心 (金庫與優先級調度)
# ==========================================
try:
    raw_keys = st.secrets["api_keys"]
    if isinstance(raw_keys, str):
        API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
    else:
        API_KEYS = list(raw_keys)
    if not API_KEYS: raise ValueError("金鑰為空")
except Exception:
    st.error("❌ 金庫 (Secrets) 尚未正確配置。")
    st.stop()

st.set_page_config(page_title="股票學術交流站", page_icon="🎯", layout="wide")

if 'key_pool' not in st.session_state:
    st.session_state.key_pool = {i: datetime.now() for i in range(len(API_KEYS))}

HISTORY_FILE = "system_history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f: return {"manual_results": json.load(f).get('manual_results', [])}
        except: pass
    return {"manual_results": []}

def save_history(manual_data):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f: json.dump({"manual_results": manual_data}, f, ensure_ascii=False, indent=4)
    except: pass

if 'db' not in st.session_state: st.session_state.db = load_history()

def delete_record(index):
    if 0 <= index < len(st.session_state.db['manual_results']):
        st.session_state.db['manual_results'].pop(index)
        save_history(st.session_state.db['manual_results'])

# ==========================================
# 3. 數據精算與爬蟲 (新增：股票名稱)
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_data(ticker):
    try: 
        tk_obj = yf.Ticker(ticker)
        df = tk_obj.history(period="1y")
        if df.empty: return None, None
        try: name = tk_obj.info.get('shortName', ticker)
        except: name = ticker
        return df, name
    except: return None, None

@st.cache_data(ttl=3600, show_spinner=False)
def get_market_return(is_tw):
    try:
        df = yf.Ticker("^TWII" if is_tw else "^GSPC").history(period="1mo")
        return (df['Close'].iloc[-1] - df['Close'].iloc[-10]) / df['Close'].iloc[-10] * 100
    except: return 0

def calculate_technical_data(df, market_ret):
    try:
        close = df['Close'].iloc[-1]
        ma5, ma10, ma20, ma60 = [df['Close'].rolling(w).mean().iloc[-1] for w in [5, 10, 20, 60]]
        ma20_yest = df['Close'].rolling(20).mean().iloc[-2]
        tr = pd.concat([df['High']-df['Low'], np.abs(df['High']-df['Close'].shift()), np.abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
        exp1, exp2 = df['Close'].ewm(span=12, adjust=False).mean(), df['Close'].ewm(span=26, adjust=False).mean()
        dif, dea = (exp1 - exp2), (exp1 - exp2).ewm(span=9, adjust=False).mean()
        low_9, high_9 = df['Low'].rolling(9).min(), df['High'].rolling(9).max()
        rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
        k = rsv.ewm(alpha=1/3).mean()
        
        return {
            "C": round(close, 2), "MAs": [round(ma5,2), round(ma10,2), round(ma20,2), round(ma60,2)],
            "T20": 1 if ma20 > ma20_yest else 0, "BIAS": round((close - ma20)/ma20*100, 2), 
            "ATR": round(tr.rolling(14).mean().iloc[-1], 2), "RS": round(((df.tail(10)['Close'].iloc[-1] - df.tail(10)['Close'].iloc[0])/df.tail(10)['Close'].iloc[0]*100) - market_ret, 2),
            "MH": round((dif - dea).iloc[-1], 2), "K": round(k.iloc[-1], 2), "D": round(k.ewm(alpha=1/3).mean().iloc[-1], 2)
        }
    except: return None

# ==========================================
# 4. 核心調度 (🛠️ 省油快取 + 影子備援)
# ==========================================
# 🛠️ 策略二：系統級指令 (最省 Token 的人設宣告)
SYS_INSTRUCT = """你是朱家泓波段長。用8大模組(均線15,型態15,壓力10,價量15,RS15,MACD10,KD10,乖離10)量化評分。
否決條件:破下彎20MA/高檔爆量長黑/破20日低。
必須回傳純JSON，鍵值：{"total_score":總分, "veto_alert":"否決原因或空", "radar_scores":[這8項的整數得分依序], "tech_breakdown":{"項目":"短評"}, "trading_plan":{"buy_zone":"","stop_loss":"","take_profit":"","risk_reward_eval":""}, "conclusion":"操作建議"}"""

def safe_generate_content(prompt_data):
    num_keys = len(API_KEYS)
    for attempt in range(num_keys * 3): 
        time.sleep(random.uniform(1.0, 2.5)) # Jitter
        
        # 🛠️ 影子備援機制：優先用前 N-1 把免費金鑰，最後一把是付費 VIP
        healthy_idx = -1
        free_keys = list(range(num_keys - 1)) if num_keys > 1 else [0]
        vip_key = num_keys - 1
        
        # 先查免費金鑰
        for idx in free_keys:
            if datetime.now() >= st.session_state.key_pool[idx]:
                healthy_idx = idx; break
        
        # 免費都在冷卻，才動用付費金鑰
        if healthy_idx == -1 and num_keys > 1 and datetime.now() >= st.session_state.key_pool[vip_key]:
            healthy_idx = vip_key
            
        if healthy_idx == -1:
            wait_sec = (min(st.session_state.key_pool.values()) - datetime.now()).total_seconds() + 2
            if wait_sec > 0:
                st.toast(f"💤 引擎冷卻中，等待 {int(wait_sec)} 秒..."); time.sleep(wait_sec); continue
        
        genai.configure(api_key=API_KEYS[healthy_idx])
        model = genai.GenerativeModel('gemini-2.5-flash', system_instruction=SYS_INSTRUCT)
        try:
            return model.generate_content(prompt_data, generation_config=genai.types.GenerationConfig(temperature=0.0))
        except Exception as e:
            st.session_state.key_pool[healthy_idx] = datetime.now() + timedelta(seconds=60)
            if "429" in str(e).lower() or "quota" in str(e).lower(): st.toast(f"⚠️ 引擎 {healthy_idx+1} 流量限制，切換備援...")
            else: time.sleep(2)
            continue
    raise Exception("所有引擎嘗試均失敗。")

def run_analysis(ticker_input):
    try:
        tk, cost = (ticker_input.split("@")[0].strip().upper(), float(ticker_input.split("@")[1].strip())) if "@" in ticker_input else (ticker_input.strip().upper(), None)
        df, name = get_stock_data(tk + ".TW") if tk.isdigit() else get_stock_data(tk)
        if df is None and tk.isdigit(): df, name = get_stock_data(tk + ".TWO")
        
        if df is None: return {"error": "無法取得報價資料"}
        ta = calculate_technical_data(df, get_market_return(".TW" in tk or ".TWO" in tk))
        if ta is None: return {"error": "指標運算異常"}
        
        # 🛠️ 策略一：機器語微縮化 (大幅降低輸入 Token)
        mini_prompt = f'{{"T":"{tk}","C":{ta["C"]},"MAs":{ta["MAs"]},"T20":{ta["T20"]},"B":{ta["BIAS"]},"ATR":{ta["ATR"]},"RS":{ta["RS"]},"MH":{ta["MH"]},"K":{ta["K"]},"D":{ta["D"]},"Cost":{cost if cost else "null"}}}'
        
        res = safe_generate_content(mini_prompt)
        raw = res.text
        parsed = json.loads(raw[raw.find('{'):raw.rfind('}')+1])
        parsed.update({'cost_price': cost, 'resolved_ticker': tk, 'stock_name': name, 'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'current_price': ta['C']})
        return parsed
    except Exception as e: return {"error": f"系統異常: {str(e)}"}

# ==========================================
# 5. UI 與圖表渲染
# ==========================================
def plot_kline(df, cost=None):
    try:
        df['5MA'], df['10MA'], df['20MA'], df['60MA'] = [df['Close'].rolling(w).mean() for w in [5, 10, 20, 60]]
        df = df.tail(60)
        fig = go.Figure(data=[go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K線')])
        for ma, color, n in [(df['5MA'], 'blue', '5MA'), (df['10MA'], 'orange', '10MA'), (df['20MA'], 'green', '20MA'), (df['60MA'], 'purple', '60MA')]:
            fig.add_trace(go.Scatter(x=df.index, y=ma, line=dict(color=color, width=1.5), name=n))
        if cost: fig.add_hline(y=cost, line_dash="dash", line_color="red", annotation_text=f"成本: {cost}")
        fig.update_layout(height=350, margin=dict(l=0,r=0,t=20,b=0), xaxis_rangeslider_visible=False)
        return fig
    except: return None

# 🛠️ 新增：朱家泓戰力雷達圖
def plot_radar(scores):
    try:
        cats = ['均線(15)', '型態(15)', '壓力(10)', '價量(15)', 'RS(15)', 'MACD(10)', 'KD(10)', '乖離(10)']
        max_s = [15, 15, 10, 15, 15, 10, 10, 10]
        if len(scores) != 8: return None
        norm = [(s/m)*100 for s, m in zip(scores, max_s)]
        norm.append(norm[0]); cats.append(cats[0]); scores.append(scores[0]) # 閉合
        
        fig = go.Figure(go.Scatterpolar(
            r=norm, theta=cats, fill='toself', fillcolor='rgba(0, 150, 255, 0.3)', line=dict(color='rgba(0, 110, 255, 0.8)', width=2),
            text=[f"得分: {s}" for s in scores], hoverinfo="text+theta", name='戰力'
        ))
        fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100], showticklabels=False)), showlegend=False, height=250, margin=dict(l=40, r=40, t=20, b=20))
        return fig
    except: return None

st.title("🎯 股票學術交流站")
st.info("**💡 戰車指南：** 直接輸入代號(如 `2330`)，持股加成本(如 `3163@400`)，逗號可批次。")

col_in, col_clear = st.columns([3, 1])
with col_in: user_in = st.text_input("請輸入診斷清單：", key="main_in", placeholder="例如: 2330, AMD@170")
with col_clear:
    st.write("<br>", unsafe_allow_html=True)
    if st.button("🗑️ 清空歷史"):
        st.session_state.db = {"manual_results": []}
        save_history([]); st.rerun()

if st.button("🚀 啟動學術診斷", type="primary", use_container_width=True):
    tickers = [t.strip() for t in user_in.split(",") if t.strip()]
    prog, status = st.progress(0), st.empty()
    for idx, tk in enumerate(tickers):
        status.info(f"⏳ 分析 {tk} ...")
        res = run_analysis(tk)
        if "error" not in res:
            st.session_state.db['manual_results'].insert(0, {"full_ticker": res['resolved_ticker'], "deep": res})
            save_history(st.session_state.db['manual_results'])
        else: st.error(f"❌ {tk} 失敗：{res['error']}")
        prog.progress((idx + 1) / len(tickers))
    st.rerun()

# 渲染報告
for i, item in enumerate(st.session_state.db['manual_results']):
    d = item['deep']
    tk, name = item['full_ticker'], d.get('stock_name', '')
    cost, c_price = d.get('cost_price'), d.get('current_price', 0)
    
    # 🛠️ 新增：即時損益標籤與外部捷徑
    pnl_tag = f"&nbsp;&nbsp;<span style='color:{'#ff4b4b' if c_price>=cost else '#00cc96'}; font-weight:bold;'>【帳面: {'+' if c_price>=cost else ''}{round((c_price-cost)/cost*100, 2)}%】</span>" if cost else ""
    links = f"&nbsp;&nbsp;<a href='https://hk.finance.yahoo.com/quote/{tk}' target='_blank' style='text-decoration:none; background:#eee; color:#333; padding:2px 8px; border-radius:12px; font-size:12px;'>Yahoo</a>&nbsp;<a href='https://tw.tradingview.com/chart/?symbol={tk.split('.')[0]}' target='_blank' style='text-decoration:none; background:#eee; color:#333; padding:2px 8px; border-radius:12px; font-size:12px;'>TradingView</a>"
    
    with st.expander(f"📌 {tk} {name}", expanded=(i==0)):
        st.markdown(f"🕒 *分析時間: {d.get('timestamp', '未知')}* {pnl_tag} {links}", unsafe_allow_html=True)
        if d.get('veto_alert'): st.error(f"🚫 否決：{d['veto_alert']}")
        
        st.markdown(f"<h1 style='text-align:center;'>{d.get('total_score', '?')} / 100</h1>", unsafe_allow_html=True)
        st.info(f"**操作建議：** {d.get('conclusion', '')}")
        
        c_left, c_right = st.columns([1, 1])
        with c_left:
            st.subheader("📊 給分細節")
            for k, v in d.get('tech_breakdown', {}).items(): st.write(f"- **{k}**: {v}")
            p = d.get('trading_plan', {})
            st.warning(f"買區: {p.get('buy_zone')}\n\n停損: {p.get('stop_loss')}\n\n停利: {p.get('take_profit')}\n\n風報: {p.get('risk_reward_eval')}")
        with c_right:
            radar_fig = plot_radar(d.get('radar_scores', []))
            if radar_fig: st.plotly_chart(radar_fig, use_container_width=True, key=f"r_{i}")
            
            df_k, _ = get_stock_data(tk)
            if df_k is not None:
                k_fig = plot_kline(df_k, cost)
                if k_fig: st.plotly_chart(k_fig, use_container_width=True, key=f"k_{i}")

        st.write("---")
        b1, b2, b3 = st.columns([1, 1, 2])
        with b1:
            if st.button("🔄 重新診斷", key=f"up_{i}", use_container_width=True):
                target = f"{tk}@{cost}" if cost else tk
                new_res = run_analysis(target)
                if "error" not in new_res:
                    st.session_state.db['manual_results'][i]['deep'] = new_res
                    save_history(st.session_state.db['manual_results']); st.rerun()
        with b2:
            if st.button("❌ 刪除紀錄", key=f"del_{i}", use_container_width=True):
                delete_record(i); st.rerun()
