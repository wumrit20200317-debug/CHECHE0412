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
import urllib.request
import re

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
# 3. 數據精算與爬蟲 (升級版偽裝爬蟲)
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def get_chinese_name(tk):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        if tk.isdigit(): 
            url = f"https://tw.stock.yahoo.com/quote/{tk}"
            req = urllib.request.Request(url, headers=headers)
            html = urllib.request.urlopen(req, timeout=3).read().decode('utf-8')
            match = re.search(r'<title>(.*?)\(', html)
            if match: return match.group(1).strip()
        else:
            url = f"https://hk.finance.yahoo.com/quote/{tk}"
            req = urllib.request.Request(url, headers=headers)
            html = urllib.request.urlopen(req, timeout=3).read().decode('utf-8')
            match = re.search(r'<title>(.*?)\s+\(', html)
            if match: return match.group(1).replace('股票價格', '').replace('今日', '').strip()
    except: pass
    return "" # 抓不到就回傳空字串，防止出現重複代號

@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_data(ticker):
    try: 
        df = yf.Ticker(ticker).history(period="1y")
        if df.empty: return None
        return df
    except: return None

@st.cache_data(ttl=3600, show_spinner=False)
def get_market_return(is_tw):
    try:
        df = yf.Ticker("^TWII" if is_tw else "^GSPC").history(period="1mo")
        return (df['Close'].iloc[-1] - df['Close'].iloc[-10]) / df['Close'].iloc[-10] * 100
    except: return 0

def calculate_technical_data(df, market_ret):
    try:
        close = df['Close'].iloc[-1]
        open_p = df['Open'].iloc[-1]
        ma5, ma10, ma20, ma60 = [df['Close'].rolling(w).mean().iloc[-1] for w in [5, 10, 20, 60]]
        ma20_yest = df['Close'].rolling(20).mean().iloc[-2]
        
        vol = df['Volume'].iloc[-1]
        vol_5ma = df['Volume'].rolling(5).mean().iloc[-1]
        
        high_20 = df['High'].tail(20).max()
        low_20 = df['Low'].tail(20).min()
        
        exp1, exp2 = df['Close'].ewm(span=12, adjust=False).mean(), df['Close'].ewm(span=26, adjust=False).mean()
        dif, dea = (exp1 - exp2), (exp1 - exp2).ewm(span=9, adjust=False).mean()
        osc = dif - dea
        osc_yest = osc.iloc[-2]
        
        low_9, high_9 = df['Low'].rolling(9).min(), df['High'].rolling(9).max()
        rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
        k = rsv.ewm(alpha=1/3).mean()
        d = k.ewm(alpha=1/3).mean()
        k_yest = k.iloc[-2]
        
        rs = ((df.tail(10)['Close'].iloc[-1] - df.tail(10)['Close'].iloc[0])/df.tail(10)['Close'].iloc[0]*100) - market_ret
        bias20 = (close - ma20)/ma20*100
        
        return {
            "C": round(close, 2), "O": round(open_p, 2), "MAs": [round(ma5,2), round(ma10,2), round(ma20,2), round(ma60,2)],
            "T20": 1 if ma20 > ma20_yest else 0, "BIAS": round(bias20, 2), 
            "RS": round(rs, 2), "Vol": vol, "Vol5": vol_5ma,
            "H20": round(high_20, 2), "L20": round(low_20, 2),
            "DIF": round(dif.iloc[-1], 2), "DEA": round(dea.iloc[-1], 2), "OSC": round(osc.iloc[-1], 2), "OSC_Y": round(osc_yest, 2),
            "K": round(k.iloc[-1], 2), "D": round(d.iloc[-1], 2), "K_Y": round(k_yest, 2)
        }
    except: return None

# ==========================================
# 4. 鐵血計分引擎 (自帶 8 大細節短評)
# ==========================================
def get_python_scores(ta):
    scores = {}
    breakdown = {}
    C, MAs, T20 = ta["C"], ta["MAs"], ta["T20"]
    
    # 1. 均線(15)
    if C > MAs[0] > MAs[1] > MAs[2] > MAs[3]: 
        scores["MA"] = 15; breakdown["均線"] = "短中長天期均線呈標準多頭排列，多方動能極強。"
    elif C > MAs[2] and T20 == 1: 
        scores["MA"] = 10; breakdown["均線"] = "股價穩站月線(20MA)之上且趨勢向上，具備波段保護力。"
    elif T20 == 1: 
        scores["MA"] = 5; breakdown["均線"] = "股價雖跌破月線，但月線仍維持上彎，視為強勢整理。"
    else: 
        scores["MA"] = 0; breakdown["均線"] = "跌破下彎的月線，短線趨勢偏空。"
    
    # 2. 型態動能(15) 
    if C >= ta["H20"]: 
        scores["Pattern"] = 15; breakdown["型態"] = "創下近20日新高，動能強勢發動突破。"
    elif C >= ta["H20"] * 0.97: 
        scores["Pattern"] = 10; breakdown["型態"] = "逼近前波高點(3%以內)，蓄勢準備挑戰突破。"
    elif C >= (ta["H20"] + ta["L20"])/2: 
        scores["Pattern"] = 5; breakdown["型態"] = "處於近期箱型整理區間的中軸之上，持續震盪。"
    else: 
        scores["Pattern"] = 0; breakdown["型態"] = "弱勢破底或處於盤整區間下緣。"
    
    # 3. 壓力/支撐(10)
    if C > MAs[3]: 
        scores["Support"] = 10; breakdown["壓力"] = "站上季線(60MA)，長線具備強烈支撐。"
    else: 
        scores["Support"] = 0; breakdown["壓力"] = "位於季線之下，上方長線壓力較為沉重。"
    
    # 4. 價量(15)
    if C > ta["O"] and ta["Vol"] > ta["Vol5"] * 1.5: 
        scores["Volume"] = 15; breakdown["價量"] = "帶量收紅，成交量大於5日均量1.5倍，主力攻擊量現。"
    elif C > ta["O"] and ta["Vol"] > ta["Vol5"]: 
        scores["Volume"] = 10; breakdown["價量"] = "溫和放量收紅，量價配合良好。"
    elif ta["Vol"] <= ta["Vol5"]: 
        scores["Volume"] = 5; breakdown["價量"] = "量縮整理，籌碼相對安定未失控。"
    else: 
        scores["Volume"] = 0; breakdown["價量"] = "爆量收黑或出現價量背離，須留意出貨風險。"
    
    # 5. RS(15)
    if ta["RS"] > 5: 
        scores["RS"] = 15; breakdown["RS"] = "近10日報酬強於大盤5%以上，為市場主流強勢股。"
    elif ta["RS"] > 0: 
        scores["RS"] = 10; breakdown["RS"] = "近期走勢優於大盤，具備相對抗跌特性。"
    else: 
        scores["RS"] = 0; breakdown["RS"] = "走勢弱於大盤，目前較不受市場資金青睞。"
    
    # 6. MACD(10)
    if ta["DIF"] > ta["DEA"] and ta["OSC"] > ta["OSC_Y"] and ta["OSC"] > 0: 
        scores["MACD"] = 10; breakdown["MACD"] = "維持多頭交叉，且紅柱狀圖持續放大，動能強勁。"
    elif ta["DIF"] > ta["DEA"] and ta["OSC"] > 0: 
        scores["MACD"] = 7; breakdown["MACD"] = "維持多頭，但紅柱狀圖已開始縮短，上攻動能略減。"
    elif ta["DIF"] < ta["DEA"] and ta["OSC"] > ta["OSC_Y"]: 
        scores["MACD"] = 3; breakdown["MACD"] = "空頭格局，但綠柱狀圖縮短，有跌深反彈契機。"
    else: 
        scores["MACD"] = 0; breakdown["MACD"] = "死亡交叉且綠柱持續放大，空方動能增強。"
    
    # 7. KD(10)
    if ta["K"] > ta["D"] and ta["K"] > ta["K_Y"]: 
        scores["KD"] = 10; breakdown["KD"] = "K>D且K值向上，短線強勢不變。"
    elif ta["K"] > ta["D"]: 
        scores["KD"] = 5; breakdown["KD"] = "維持黃金交叉，但K值略微下彎轉弱。"
    else: 
        scores["KD"] = 0; breakdown["KD"] = "死亡交叉，短線進入弱勢整理。"
    
    # 8. 乖離(10)
    if 0 <= ta["BIAS"] <= 8: 
        scores["BIAS"] = 10; breakdown["乖離"] = "正乖離介於0~8%的安全起漲區間內。"
    elif 8 < ta["BIAS"] <= 15: 
        scores["BIAS"] = 5; breakdown["乖離"] = "正乖離偏高，短線有過熱拉回風險。"
    else: 
        scores["BIAS"] = 0; breakdown["乖離"] = "乖離率過大或呈現負乖離破線狀態。"
    
    total = sum(scores.values())
    radar = [scores["MA"], scores["Pattern"], scores["Support"], scores["Volume"], scores["RS"], scores["MACD"], scores["KD"], scores["BIAS"]]
    return total, radar, breakdown

# ==========================================
# 5. 核心調度 (終極壓縮 AI)
# ==========================================
SYS_INSTRUCT = """你是朱家泓波段長。以下是客觀技術分數(滿分100)。
請嚴格回傳純JSON。鍵值：{"trading_plan":{"buy_zone":"建議買區","stop_loss":"停損價位","take_profit":"停利預估","risk_reward_eval":"風報比簡評"}, "conclusion":"綜合操作建議", "veto_alert":"破20MA或爆量長黑則填寫否決，否則填無"}"""

def safe_generate_content(prompt_data):
    num_keys = len(API_KEYS)
    for attempt in range(num_keys * 3): 
        time.sleep(random.uniform(1.0, 2.5)) 
        
        healthy_idx = -1
        free_keys = list(range(num_keys - 1)) if num_keys > 1 else [0]
        vip_key = num_keys - 1
        
        for idx in free_keys:
            if datetime.now() >= st.session_state.key_pool[idx]:
                healthy_idx = idx; break
        
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
            if "429" in str(e).lower() or "quota" in str(e).lower(): st.toast(f"⚠️ 引擎 {healthy_idx+1} 流量限制...")
            else: time.sleep(2)
            continue
    raise Exception("所有引擎嘗試均失敗。")

def run_analysis(ticker_input):
    try:
        tk, cost = (ticker_input.split("@")[0].strip().upper(), float(ticker_input.split("@")[1].strip())) if "@" in ticker_input else (ticker_input.strip().upper(), None)
        
        yahoo_tk = tk + ".TW" if tk.isdigit() else tk
        df = get_stock_data(yahoo_tk)
        if df is None and tk.isdigit(): 
            yahoo_tk = tk + ".TWO"
            df = get_stock_data(yahoo_tk)
        
        if df is None: return {"error": "無法取得報價資料"}
        ta = calculate_technical_data(df, get_market_return(".TW" in tk or ".TWO" in tk))
        if ta is None: return {"error": "指標運算異常"}
        
        # 修正：直接傳入 tk 進行判斷，並抓取正確中文名
        chinese_name = get_chinese_name(tk)
        
        # 取得總分、雷達數值與 8 大細節短評
        total_score, radar_array, py_breakdown = get_python_scores(ta)
        
        # 極致省油 Prompt (不再要求 AI 寫 breakdown)
        mini_prompt = f'{{"T":"{tk}","C":{ta["C"]},"Score":{total_score},"Radar":{radar_array},"MAs":{ta["MAs"]},"B":{ta["BIAS"]}}}'
        
        res = safe_generate_content(mini_prompt)
        raw = res.text
        parsed = json.loads(raw[raw.find('{'):raw.rfind('}')+1])
        
        # 將 Python 產生的細節合併進去
        parsed.update({
            'tech_breakdown': py_breakdown,
            'total_score': total_score, 'radar_scores': radar_array,
            'cost_price': cost, 'resolved_ticker': tk, 'yahoo_ticker': yahoo_tk, 
            'stock_name': chinese_name, 'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'current_price': ta['C']
        })
        return parsed
    except Exception as e: return {"error": f"系統異常: {str(e)}"}

# ==========================================
# 6. UI 與圖表渲染
# ==========================================
def plot_kline(df, cost=None):
    try:
        df['5MA'], df['10MA'], df['20MA'], df['60MA'] = [df['Close'].rolling(w).mean() for w in [5, 10, 20, 60]]
        df = df.tail(60)
        fig = go.Figure(data=[go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='日K線')])
        for ma, color, n in [(df['5MA'], 'blue', '5日線'), (df['10MA'], 'orange', '10日線'), (df['20MA'], 'green', '月線(20日)'), (df['60MA'], 'purple', '季線(60日)')]:
            fig.add_trace(go.Scatter(x=df.index, y=ma, line=dict(color=color, width=1.5), name=n))
        if cost: fig.add_hline(y=cost, line_dash="dash", line_color="red", annotation_text=f"成本: {cost}")
        fig.update_layout(height=350, margin=dict(l=0,r=0,t=20,b=0), xaxis_rangeslider_visible=False, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        return fig
    except: return None

def plot_radar(scores):
    try:
        cats = ['均線(15)', '型態(15)', '壓力(10)', '價量(15)', 'RS(15)', 'MACD(10)', 'KD(10)', '乖離(10)']
        max_s = [15, 15, 10, 15, 15, 10, 10, 10]
        if len(scores) != 8: return None
        norm = [(s/m)*100 for s, m in zip(scores, max_s)]
        norm.append(norm[0]); cats.append(cats[0]); scores.append(scores[0])
        
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

if st.button("🚀 啟動學術診斷", type="
