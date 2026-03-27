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
        
        # 1. 잔고현황 데이터 가져오기
        balance_sheet = spreadsheet.worksheet("잔고현황")
        records = balance_sheet.get_all_records()
        
        # 2. 시황기록 데이터 가져오기 (마지막 줄 = 최신 지표)
        market_sheet = spreadsheet.worksheet("시황기록")
        market_records = market_sheet.get_all_records()
        latest_m = market_records[-1] if market_records else {}

        portfolio = []
        for row in records:
            acc = row.get("계좌명", "")
            if not acc: continue
            
            # 숫자 데이터 정제 함수
            def cn(v): 
                if not v: return 0
                return float(str(v).replace(',','').replace('%',''))
            
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
                "date": latest_m.get("날짜", "N/A"),
                "dow": latest_m.get("다우지수", "-"),
                "snp": latest_m.get("S&P500", "-"),
                "nasdaq": latest_m.get("나스닥", "-"),
                "us10y": latest_m.get("10년물 금리", "-"),
                "wti": latest_m.get("WTI 유가", "-"),
                "gold": latest_m.get("금", "-"),
                "usd": latest_m.get("원달러환율", "-")
            }
        }
    except Exception as e:
        print(f"데이터 로드 실패: {e}")
        return None

def get_gemini_analysis(data):
    secrets = load_secrets()
    genai.configure(api_key=secrets["gemini"]["api_key"])
    # 쿼터가 가장 안정적인 pro-latest 모델 사용
    model = genai.GenerativeModel('models/gemini-pro-latest')
    
    prompt = f"""
    당신은 Jong의 수석 자산관리 AI입니다. 
    오늘의 시장 지표: {json.dumps(data['market'], ensure_ascii=False)}
    현재 포트폴리오 상태: {json.dumps(data['portfolio'], ensure_ascii=False)}
    
    [미션]
    1. 시장 지표가 현재 포트폴리오(특히 삼성전자, 나스닥 ETF 등)에 미치는 구체적 영향을 분석하세요.
    2. 뉴스 섹션에는 글로벌 핵심 경제 뉴스 2가지를 헤드라인(t)과 상세내용(c)으로 생성하세요.
    3. 반드시 아래 JSON 형식으로만 응답하세요:
    {{
      "title": "시황 요약 한 줄",
      "macro": "시황이 내 자산에 미치는 구체적 영향 분석",
      "strategy": "주요 종목 성과 및 계좌별 대응 전략",
      "news": [
        {{"t": "뉴스제목1", "c": "뉴스내용1"}},
        {{"t": "뉴스제목2", "c": "뉴스내용2"}}
      ]
    }}
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
        supabase = get_supabase()
        supabase.table("daily_reports").upsert({"date": today, **analysis}).execute()
        return jsonify({"status": "success", "date": today})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/get_report_dates')
def get_report_dates():
    supabase = get_supabase()
    res = supabase.table("daily_reports").select("date").order("date", desc=True).execute()
    return jsonify([item['date'] for item in res.data])

@app.route('/api/get_report/<date>')
def get_report(date):
    supabase = get_supabase()
    res = supabase.table("daily_reports").select("*").eq("date", date).execute()
    return jsonify(res.data[0] if res.data else {"status": "not_found"})

if __name__ == '__main__':
    app.run(debug=True)