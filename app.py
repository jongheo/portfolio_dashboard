from flask import Flask, render_template, jsonify
import gspread
from google.oauth2.service_account import Credentials
import toml
import os

app = Flask(__name__)

# secrets.toml 파일 읽기 (Streamlit에서 쓰던 방식을 Flask에서도 호환되게 사용)
def load_secrets():
    secrets_path = os.path.join(".streamlit", "secrets.toml")
    return toml.load(secrets_path)

def get_sheet_data():
    """구글 시트(자산관리시트_250301)에서 데이터를 읽어옵니다."""
    try:
        secrets = load_secrets()
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        credentials = Credentials.from_service_account_info(secrets["gcp_service_account"], scopes=scopes)
        client = gspread.authorize(credentials)
        
        sheet = client.open("자산관리시트_250301").worksheet("잔고현황")
        records = sheet.get_all_records()
        
        # 프론트엔드 JS 변수명에 맞게 데이터 키값 변환
        formatted_data = []
        for row in records:
            formatted_data.append({
                "account": row.get("계좌명", ""),
                "asset": row.get("종목명", ""),
                "buyPrice": row.get("매입단가", 0),
                "currentPrice": row.get("현재가", 0),
                "return": round(((row.get("현재가", 0) - row.get("매입단가", 0)) / row.get("매입단가", 1)) * 100, 2),
                "value": row.get("평가금액", 0),
                "curWeight": row.get("현재비중(%)", 0),
                "targetWeight": row.get("목표비중(%)", 0),
                "guide": row.get("가이드", "비중 유지"),
                "guideType": "buy" if "확대" in row.get("가이드", "") else ("sell" if "실현" in row.get("가이드", "") else "hold")
            })
        return formatted_data
    except Exception as e:
        print(f"구글 시트 연동 오류: {e}")
        # 오류 시 기본 Fallback 데이터 제공 (티커 309230 반영)
        return [
            {"account": "종합계좌", "asset": "삼성전자", "buyPrice": 189000, "currentPrice": 180600, "return": -4.44, "value": 4200000, "curWeight": 42, "targetWeight": 45, "guide": "비중 확대", "guideType": "buy"},
            {"account": "종합계좌", "asset": "메리츠금융지주", "buyPrice": 111400, "currentPrice": 114800, "return": 3.05, "value": 5800000, "curWeight": 58, "targetWeight": 55, "guide": "일부 실현", "guideType": "sell"},
            {"account": "연금저축1", "asset": "ACE 미국빅테크TOP7 Plus (309230)", "buyPrice": 29500, "currentPrice": 29510, "return": 0.03, "value": 10000000, "curWeight": 100, "targetWeight": 100, "guide": "비중 유지", "guideType": "hold"}
        ]

@app.route('/')
def home():
    """사용자 접속 시 index.html 화면을 렌더링합니다."""
    return render_template('index.html')

@app.route('/api/portfolio')
def get_portfolio():
    """프론트엔드에서 데이터를 요청할 때 구글 시트 데이터를 JSON으로 반환합니다."""
    data = get_sheet_data()
    return jsonify(data)

if __name__ == '__main__':
    app.run(debug=True, port=5000)