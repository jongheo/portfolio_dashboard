from flask import Flask, render_template, jsonify, request
import gspread
from google.oauth2.service_account import Credentials
import toml
import os
import json
from datetime import datetime
import google.generativeai as genai
from supabase import create_client, Client

app = Flask(__name__)

def load_secrets():
    render_secret_path = "/etc/secrets/secrets.toml"
    local_secret_path = os.path.join(".streamlit", "secrets.toml")
    if os.path.exists(render_secret_path):
        return toml.load(render_secret_path)
    return toml.load(local_secret_path)

def get_supabase():
    secrets = load_secrets()
    return create_client(secrets["supabase"]["url"], secrets["supabase"]["key"])

def get_sheet_data():
    try:
        secrets = load_secrets()
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        credentials = Credentials.from_service_account_info(secrets["gcp_service_account"], scopes=scopes)
        client = gspread.authorize(credentials)
        
        spreadsheet = client.open("자산관리시트_250301")
        
        # 1. 잔고현황 데이터
        balance_sheet = spreadsheet.worksheet("잔고현황")
        records = balance_sheet.get_all_records()
        
        # 2. 시황기록 데이터 (최신 데이터 2행 읽기)
        market_sheet = spreadsheet.worksheet("시황기록")
        market_records = market_sheet.get_all_records()
        # 가장 최근에 기록된 행(마지막 행)을 가져옵니다.
        latest_market = market_records[-1] if market_records else {}

        portfolio = []
        for row in records:
            acc = row.get("계좌명", "")
            if not acc: continue
            
            def cn(v): return float(str(v).replace(',','').replace('%','')) if v and str(v).strip() != "" else 0
            
            buy_p = cn(row.get("매입단가"))
            curr_p = cn(row.get("현재가"))
            qty = cn(row.get("보유수량"))
            val = cn(row.get("평가금액"))
            ret = round(((curr_p / buy_p) - 1) * 100, 2) if buy_p > 0 else 0
            
            portfolio.append({
                "account": acc,
                "asset": row.get("종목명", ""),
                "quantity": qty,
                "buyPrice": buy_p,
                "currentPrice": curr_p,
                "return": ret,
                "value": val
            })

        return {
            "portfolio": portfolio,
            "market": {
                "date": str(latest_market.get("날짜", "-")),
                "dow": latest_market.get("다우지수", "-"),
                "snp": latest_market.get("S&P500", "-"),
                "nasdaq": latest_market.get("나스닥", "-"),
                "russell": latest_market.get("Russell2000", "-"),
                "us10y": latest_market.get("10년물 금리", "-"),
                "wti": latest_m = latest_market.get("WTI 유가", "-"),
                "gold": latest_market.get("금", "-"),
                "usd": latest_market.get("원달러환율", "-")
            }
        }
    except Exception as e:
        print(f"Sheet Error: {e}")
        return None

def get_gemini_analysis(data):
    secrets = load_secrets()
    genai.configure(api_key=secrets["gemini"]["api_key"])
    # 쿼터와 호환성이 가장 검증된 모델명 사용
    model = genai.GenerativeModel('gemini-pro') 
    
    prompt = f"""
    당신은 자산관리사입니다. 아래 데이터를 분석하여 JSON으로만 답하세요.
    시황: {json.dumps(data['market'], ensure_ascii=False)}
    포트폴리오: {json.dumps(data['portfolio'], ensure_ascii=False)}
    
    형식: {{"title": "...", "macro": "...", "strategy": "...", "news": [{{"t": "...", "c": "..."}}]}}
    """
    response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
    return json.loads(response.text.strip())

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/portfolio')
def get_portfolio(): return jsonify(get_sheet_data())

@app.route('/api/generate_daily_report', methods=['POST'])
def generate_daily_report():
    try:
        data = get_sheet_data()
        analysis = get_gemini_analysis(data)
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Supabase에 리포트와 당시 지표를 함께 저장
        report_payload = {
            "date": today, "title": analysis['title'], "macro": analysis['macro'],
            "strategy": analysis['strategy'], "news": analysis['news'],
            "dow": data['market']['dow'], "snp": data['market']['snp'], 
            "nasdaq": data['market']['nasdaq'], "russell": data['market']['russell'],
            "gold": data['market']['gold'], "usd": data['market']['usd']
        }
        get_supabase().table("daily_reports").upsert(report_payload).execute()
        return jsonify({"status": "success", "date": today})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/get_report_dates')
def get_report_dates():
    res = get_supabase().table("daily_reports").select("date").order("date", desc=True).execute()
    return jsonify([item['date'] for item in res.data])

@app.route('/api/get_report/<date>')
def get_report(date):
    res = get_supabase().table("daily_reports").select("*").eq("date", date).execute()
    return jsonify(res.data[0] if res.data else {})

if __name__ == '__main__':
    app.run(debug=True)