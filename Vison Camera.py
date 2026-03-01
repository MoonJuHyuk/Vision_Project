import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import datetime
import os
import time
import altair as alt
import base64
import numpy as np
import io
import random

# --- 0. 아이콘 설정 함수 ---
def add_apple_touch_icon(image_path):
    try:
        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                b64_icon = base64.b64encode(f.read()).decode("utf-8")
                st.markdown(
                    f"""
                    <head>
                        <link rel="icon" type="image/png" href="data:image/png;base64,{b64_icon}">
                        <link rel="shortcut icon" href="data:image/png;base64,{b64_icon}">
                        <link rel="apple-touch-icon" href="data:image/png;base64,{b64_icon}">
                        <link rel="apple-touch-icon" sizes="180x180" href="data:image/png;base64,{b64_icon}">
                        <link rel="icon" sizes="192x192" href="data:image/png;base64,{b64_icon}">
                    </head>
                    """,
                    unsafe_allow_html=True
                )
    except Exception: pass

# --- 1. 페이지 설정 ---
if os.path.exists("logo.png"):
    st.set_page_config(page_title="KPR ERP", page_icon="logo.png", layout="wide")
    add_apple_touch_icon("logo.png")
else:
    st.set_page_config(page_title="KPR ERP", page_icon="🏭", layout="wide")

# --- 2. 구글 시트 연결 ---
@st.cache_resource
def get_connection():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    spreadsheet_id = "1qLWcLwS-aTBPeCn39h0bobuZlpyepfY5Hqn-hsP-hvk"
    try:
        if "gcp_service_account" in st.secrets:
            key_dict = dict(st.secrets["gcp_service_account"])
            creds = Credentials.from_service_account_info(key_dict, scopes=scopes)
            client = gspread.authorize(creds)
            return client.open_by_key(spreadsheet_id)
    except Exception: pass
    key_file = 'key.json'
    if os.path.exists(key_file):
        creds = Credentials.from_service_account_file(key_file, scopes=scopes)
        client = gspread.authorize(creds)
        return client.open_by_key(spreadsheet_id)
    return None

doc = get_connection()

def get_sheet(doc, name, create_headers=None):
    if doc is None: return None
    try:
        return doc.worksheet(name)
    except:
        if create_headers:
            try:
                ws = doc.add_worksheet(title=name, rows="1000", cols="20")
                ws.append_row(create_headers)
                return ws
            except: return None
        return None

_orders_headers   = ['주문번호', '날짜', '거래처', '코드', '품목명', '수량', '팔레트번호', '상태', '비고', '거래처코드', '타입']
_ww_headers       = ['날짜', '대표자', '환경기술인', '가동시간', '플라스틱재생칩', '합성수지', '안료', '용수사용량', '폐수발생량', '위탁량', '기타']
_mtg_headers      = ['ID', '작성일', '공장', '안건내용', '담당자', '상태', '비고']

@st.cache_resource
def get_all_sheets():
    d = get_connection()
    if d is None:
        return {k: None for k in ['items','inventory','logs','bom','orders','wastewater','meetings']}
    return {
        'items':      get_sheet(d, 'Items'),
        'inventory':  get_sheet(d, 'Inventory'),
        'logs':       get_sheet(d, 'Logs'),
        'bom':        get_sheet(d, 'BOM'),
        'orders':     get_sheet(d, 'Orders', _orders_headers),
        'wastewater': get_sheet(d, 'Wastewater', _ww_headers),
        'meetings':   get_sheet(d, 'Meetings', _mtg_headers),
    }

_sh = get_all_sheets()
sheet_items      = _sh['items']
sheet_inventory  = _sh['inventory']
sheet_logs       = _sh['logs']
sheet_bom        = _sh['bom']
sheet_orders     = _sh['orders']
sheet_wastewater = _sh['wastewater']
sheet_meetings   = _sh['meetings']

ww_headers  = _ww_headers
mtg_headers = _mtg_headers

# --- 3. 데이터 로딩 ---
@st.cache_data(ttl=60)
def load_data():
    data = []
    sheets = [sheet_items, sheet_inventory, sheet_logs, sheet_bom, sheet_orders, sheet_wastewater, sheet_meetings]
    for s in sheets:
        df = pd.DataFrame()
        if s:
            for attempt in range(5):
                try:
                    d = s.get_all_records()
                    if d:
                        df = pd.DataFrame(d)
                        df = df.replace([np.inf, -np.inf], np.nan).fillna("")
                        if '수량' in df.columns:
                            df['수량'] = pd.to_numeric(df['수량'], errors='coerce').fillna(0.0)
                    break
                except: time.sleep(1)
        data.append(df)

    try:
        s_map = get_sheet(doc, 'Print_Mapping')
        if s_map: df_map = pd.DataFrame(s_map.get_all_records())
        else: df_map = pd.DataFrame(columns=['Code', 'Print_Name'])
    except: df_map = pd.DataFrame(columns=['Code', 'Print_Name'])

    data.append(df_map)
    return tuple(data)

def safe_float(val):
    try: return float(val)
    except: return 0.0

# --- 4. 재고 업데이트 (버그수정: sleep 최소화, 부호 로직 명확화) ---
def update_inventory(factory, code, qty, p_name="-", p_spec="-", p_type="-", p_color="-", p_unit="-"):
    """
    qty > 0 : 재고 증가 (입고, 생산, 취소 복구)
    qty < 0 : 재고 감소 (출고, 사용, 삭제)
    """
    if not sheet_inventory: return
    try:
        cells = sheet_inventory.findall(str(code))
        target = None
        if cells:
            for c in cells:
                if c.col == 2: target = c; break
        if target:
            curr = safe_float(sheet_inventory.cell(target.row, 7).value)
            sheet_inventory.update_cell(target.row, 7, round(curr + qty, 4))
        else:
            # 신규 품목은 qty가 양수일 때만 추가 (음수면 데이터 오류)
            if qty > 0:
                sheet_inventory.append_row([factory, code, p_name, p_spec, p_type, p_color, qty])
    except Exception as e:
        st.warning(f"재고 업데이트 오류 ({code}): {e}")

# --- 5. 헬퍼 함수 ---
def get_shape(code, df_items):
    shape = "-"
    if not df_items.empty:
        item_row = df_items[df_items['코드'].astype(str) == str(code)]
        if not item_row.empty:
            korean_type = str(item_row.iloc[0].get('타입', '-'))
            if "원통" in korean_type: shape = "CYLINDRIC"
            elif "큐빅" in korean_type: shape = "CUBICAL"
            elif "펠렛" in korean_type: shape = "PELLET"
            elif "파우더" in korean_type: shape = "POWDER"
            else: shape = korean_type
    return shape

def create_print_button(html_content, title="Print", orientation="portrait"):
    safe_content = html_content.replace('`', '\\`').replace('$', '\\$')
    page_css = "@page { size: A4 portrait; margin: 1cm; }"
    if orientation == "landscape": page_css = "@page { size: A4 landscape; margin: 1cm; }"
    func_name = title.replace(" ", "_").replace("/", "_")
    js_code = f"""<script>
    function print_{func_name}() {{
        var win = window.open('', '', 'width=900,height=700');
        win.document.write('<html><head><title>{title}</title><style>{page_css} body {{ font-family: sans-serif; margin: 0; padding: 0; }} table {{ border-collapse: collapse; width: 100%; }} th, td {{ border: 1px solid black; padding: 4px; }} .page-break {{ page-break-after: always; }}</style></head><body>');
        win.document.write(`{safe_content}`);
        win.document.write('</body></html>');
        win.document.close();
        win.focus();
        setTimeout(function() {{ win.print(); }}, 500);
    }}
    </script>
    <button onclick="print_{func_name}()" style="background-color:#4CAF50;border:none;color:white;padding:10px 20px;font-size:14px;margin:4px 2px;cursor:pointer;border-radius:5px;">🖨️ {title} 인쇄하기</button>"""
    return js_code

def get_product_category(row):
    name  = str(row['품목명']).upper()
    code  = str(row['코드']).upper()
    gubun = str(row.get('구분', '')).strip()
    if 'CP' in name or 'COMPOUND' in name or 'CP' in code: return "Compound"
    if ('KA' in name or 'KA' in code) and (gubun == '반제품' or name.endswith('반') or '반' in name): return "KA반제품"
    if 'KA' in name or 'KA' in code: return "KA"
    if 'KG' in name or 'KG' in code: return "KG"
    if gubun == '반제품' or name.endswith('반'): return "반제품(기타)"
    return "기타"

# --- 6. 로그인 (버그수정: text_input을 button보다 먼저 렌더링) ---
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    st.title("🔒 KPR ERP 시스템")
    c1, c2 = st.columns([1, 2])
    with c1:
        # ✅ text_input을 button보다 먼저 배치해야 값이 정상 전달됨
        pw_input = st.text_input("접속 암호", type="password", key="login_pw")
        if st.button("로그인", type="primary"):
            # 환경변수 또는 secrets에서 암호 확인 (없으면 기본값 사용)
            correct_pw = st.secrets.get("app_password", "kpr1234") if hasattr(st, 'secrets') else "kpr1234"
            if pw_input == correct_pw:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("암호가 틀렸습니다.")
    st.stop()

df_items, df_inventory, df_logs, df_bom, df_orders, df_wastewater, df_meetings, df_mapping = load_data()
if 'cart' not in st.session_state: st.session_state['cart'] = []
if 'edit_mode' not in st.session_state: st.session_state['edit_mode'] = False

# --- 7. 사이드바 ---
with st.sidebar:
    if os.path.exists("logo.png"): st.image("logo.png", use_container_width=True)
    else: st.header("🏭 KPR / Chamstek")
    if st.button("🔄 새로고침"): st.cache_data.clear(); st.cache_resource.clear(); st.rerun()
    st.markdown("---")
    menu = st.radio("메뉴", [
        "대시보드",
        "재고/생산 관리",
        "영업/출고 관리",
        "🏭 현장 작업 (LOT 입력)",
        "🔍 이력/LOT 검색",
        "🌊 환경/폐수 일지",
        "📋 주간 회의 & 개선사항"
    ])
    st.markdown("---")
    date     = st.date_input("날짜", datetime.datetime.now())
    time_str = datetime.datetime.now().strftime("%H:%M:%S")
    factory  = st.selectbox("공장", ["1공장", "2공장"])

# ==================== [0] 대시보드 ====================
if menu == "대시보드":
    st.title("📊 공장 현황 대시보드")
    if not df_logs.empty:
        today = datetime.date.today()
        target_date_str = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        display_label = "어제"

        if '구분' in df_logs.columns and '날짜' in df_logs.columns:
            prod_dates = df_logs[df_logs['구분'] == '생산']['날짜'].unique()
            if len(prod_dates) > 0:
                prod_dates = sorted(prod_dates, reverse=True)
                for d_str in prod_dates:
                    try:
                        d_date = pd.to_datetime(d_str).date()
                        if d_date < today:
                            target_date_str = d_str
                            display_label = "어제" if d_date == today - datetime.timedelta(days=1) else "최근 작업일"
                            break
                    except: continue

        df_target_day = df_logs[df_logs['날짜'] == target_date_str]
        prod_data = df_target_day[df_target_day['구분'] == '생산'].copy() if '구분' in df_target_day.columns else pd.DataFrame()

        total_prod = ka_prod = kg_prod = ka_ban_prod = cp_prod = 0
        if not prod_data.empty:
            prod_data['Category'] = prod_data.apply(get_product_category, axis=1)
            total_prod  = prod_data['수량'].sum()
            ka_prod     = prod_data[prod_data['Category'] == 'KA']['수량'].sum()
            kg_prod     = prod_data[prod_data['Category'] == 'KG']['수량'].sum()
            ka_ban_prod = prod_data[prod_data['Category'] == 'KA반제품']['수량'].sum()
            cp_prod     = prod_data[prod_data['Category'] == 'Compound']['수량'].sum()

        out_val  = df_target_day[df_target_day['구분'] == '출고']['수량'].sum() if '구분' in df_target_day.columns else 0
        pend_cnt = len(df_orders[df_orders['상태'] == '준비']['주문번호'].unique()) if not df_orders.empty and '상태' in df_orders.columns else 0

        st.subheader(f"📅 {display_label}({target_date_str}) 실적 요약")
        k1, k2, k3 = st.columns(3)
        k1.metric(f"{display_label} 총 생산", f"{total_prod:,.0f} kg")
        k1.markdown(f"<div style='font-size:14px;color:gray;'>• KA: {ka_prod:,.0f} kg<br>• KG: {kg_prod:,.0f} kg<br>• KA반제품: {ka_ban_prod:,.0f} kg<br>• Compound: {cp_prod:,.0f} kg</div>", unsafe_allow_html=True)
        k2.metric(f"{display_label} 총 출고", f"{abs(out_val):,.0f} kg")
        k3.metric("출고 대기 주문", f"{pend_cnt} 건", delta="작업 필요", delta_color="inverse")

        # 이번달 누적 생산량 요약
        this_month_str = today.strftime("%Y-%m")
        df_this_month  = df_logs[(df_logs['구분'] == '생산') & (df_logs['날짜'].astype(str).str.startswith(this_month_str))]
        monthly_total  = df_this_month['수량'].sum()
        working_days   = df_this_month['날짜'].nunique()
        avg_daily      = monthly_total / working_days if working_days > 0 else 0

        st.markdown(f"""
        <div style='background:#f0f4ff;border-radius:10px;padding:12px 20px;margin:8px 0;display:flex;gap:40px;'>
            <div><span style='color:#888;font-size:13px;'>📅 {this_month_str} 누적 생산</span><br>
            <span style='font-size:22px;font-weight:bold;color:#1a5cad;'>{monthly_total:,.0f} kg</span></div>
            <div><span style='color:#888;font-size:13px;'>🏭 작업일수</span><br>
            <span style='font-size:22px;font-weight:bold;color:#1a5cad;'>{working_days}일</span></div>
            <div><span style='color:#888;font-size:13px;'>📊 일평균 생산</span><br>
            <span style='font-size:22px;font-weight:bold;color:#1a5cad;'>{avg_daily:,.0f} kg</span></div>
        </div>""", unsafe_allow_html=True)
        st.markdown("---")

        if '구분' in df_logs.columns:
            st.subheader("📈 생산 추이 분석 (제품군별 비교)")
            c_filter1, c_filter2 = st.columns([2, 1])
            with c_filter1:
                target_dt_obj = pd.to_datetime(target_date_str).date()
                week_ago = target_dt_obj - datetime.timedelta(days=6)
                search_range = st.date_input("조회 기간 설정", [week_ago, target_dt_obj])
            with c_filter2:
                filter_opt = st.selectbox("조회 품목 필터", ["전체", "KA", "KG", "KA반제품", "Compound"])

            df_prod_log = df_logs[df_logs['구분'] == '생산'].copy()
            if len(search_range) == 2:
                s_d, e_d = search_range
                all_dates  = pd.date_range(start=s_d, end=e_d)
                categories = ["KA", "KG", "KA반제품", "Compound", "기타"]
                skeleton_data = [{'날짜': d.strftime('%Y-%m-%d'), 'Category': c, '수량': 0} for d in all_dates for c in categories]
                df_skeleton = pd.DataFrame(skeleton_data)

                if not df_prod_log.empty:
                    df_prod_log['날짜'] = pd.to_datetime(df_prod_log['날짜']).dt.strftime('%Y-%m-%d')
                    df_prod_log['Category'] = df_prod_log.apply(get_product_category, axis=1)
                    if filter_opt != "전체": df_prod_log = df_prod_log[df_prod_log['Category'] == filter_opt]
                    real_sum = df_prod_log.groupby(['날짜', 'Category'])['수량'].sum().reset_index()
                else:
                    real_sum = pd.DataFrame(columns=['날짜', 'Category', '수량'])

                if filter_opt != "전체": df_skeleton = df_skeleton[df_skeleton['Category'] == filter_opt]
                final_df = pd.merge(df_skeleton, real_sum, on=['날짜', 'Category'], how='left', suffixes=('_base', '_real'))
                final_df['수량'] = final_df['수량_real'].fillna(0)
                final_df['날짜_dt'] = pd.to_datetime(final_df['날짜'])
                weekday_map = {0:'(월)', 1:'(화)', 2:'(수)', 3:'(목)', 4:'(금)', 5:'(토)', 6:'(일)'}
                final_df['요일'] = final_df['날짜_dt'].dt.dayofweek.map(weekday_map)
                final_df['표시날짜'] = final_df['날짜_dt'].dt.strftime('%m-%d') + " " + final_df['요일']

                domain = ["KA", "KG", "KA반제품", "Compound", "기타"]
                range_ = ["#1f77b4", "#ff7f0e", "#17becf", "#d62728", "#9467bd"]
                chart = alt.Chart(final_df).mark_bar().encode(
                    x=alt.X('표시날짜', title='날짜 (요일)', axis=alt.Axis(labelAngle=0)),
                    y=alt.Y('수량', title='생산량 (KG)'),
                    color=alt.Color('Category', scale=alt.Scale(domain=domain, range=range_), title='제품군'),
                    xOffset='Category',
                    tooltip=['표시날짜', 'Category', alt.Tooltip('수량', format=',.0f')]
                ).properties(height=350)
                st.altair_chart(chart, use_container_width=True)

                st.markdown("---")
                st.subheader("📥 최근 10일 원재료 입고 리포트")
                df_inbound_all = df_logs[df_logs['구분'] == '입고'].copy()
                if not df_inbound_all.empty:
                    in_dates    = sorted(df_inbound_all['날짜'].unique(), reverse=True)[:10]
                    df_in_10days = df_inbound_all[df_inbound_all['날짜'].isin(in_dates)].copy()
                    if not df_in_10days.empty:
                        in_chart = alt.Chart(df_in_10days).mark_bar().encode(
                            x=alt.X('날짜:N', title='입고일', sort=alt.SortField('날짜', order='descending')),
                            y=alt.Y('sum(수량):Q', title='입고량 (KG)'),
                            color=alt.Color('품목명:N', title='품목명', scale=alt.Scale(scheme='category20')),
                            tooltip=['날짜', '품목명', alt.Tooltip('sum(수량)', format=',.0f', title='총 입고량')]
                        ).properties(height=300)
                        st.altair_chart(in_chart, use_container_width=True)
                        safe_cols = [c for c in ['날짜', '시간', '코드', '품목명', '규격', '수량', '비고'] if c in df_in_10days.columns]
                        st.dataframe(df_in_10days[safe_cols].sort_values(['날짜', '시간'], ascending=False), use_container_width=True, hide_index=True)
                    else: st.info("표시할 입고 내역이 없습니다.")
                else: st.info("입고 데이터가 존재하지 않습니다.")
            else: st.info("기간을 선택해주세요.")
    else: st.info("데이터를 불러오는 중입니다...")

# ==================== [1] 재고/생산 관리 ====================
elif menu == "재고/생산 관리":
    with st.sidebar:
        st.markdown("### 📝 작업 입력")
        cat = st.selectbox("구분", ["입고", "생산", "재고실사"])
        sel_code = None; item_info = None; sys_q = 0.0
        prod_line = "-"
        if cat == "생산":
            line_options = []
            if factory == "1공장":   line_options = [f"압출{i}호" for i in range(1, 6)] + ["기타"]
            elif factory == "2공장": line_options = [f"압출{i}호" for i in range(1, 7)] + [f"컷팅{i}호" for i in range(1, 11)] + ["기타"]
            prod_line = st.selectbox("설비 라인", line_options)

        if not df_items.empty:
            df_f = df_items.copy()
            for c in ['규격', '타입', '색상', '품목명', '구분', 'Group']:
                if c in df_f.columns: df_f[c] = df_f[c].astype(str).str.strip()
            if cat == "입고":   df_f = df_f[df_f['구분'] == '원자재']
            elif cat == "생산": df_f = df_f[df_f['구분'].isin(['제품', '완제품', '반제품'])]

            def get_group(row):
                name = str(row['품목명']).upper(); grp = str(row['구분'])
                if grp == '반제품' or name.endswith('반'): return "반제품"
                if "CP" in name or "COMPOUND" in name: return "COMPOUND"
                if "KG" in name: return "KG"
                if "KA" in name: return "KA"
                return "기타"

            df_f['Group'] = df_f.apply(get_group, axis=1)
            if not df_f.empty:
                grp_list = sorted(list(set(df_f['Group'])))
                grp      = st.selectbox("1.그룹", grp_list)
                df_step1 = df_f[df_f['Group'] == grp]
                final    = pd.DataFrame()

                if grp == "반제품":
                    p_name = st.selectbox("2.품목명", sorted(list(set(df_step1['품목명']))))
                    final  = df_step1[df_step1['품목명'] == p_name]
                elif grp == "COMPOUND":
                    clr   = st.selectbox("2.색상", sorted(list(set(df_step1['색상']))))
                    final = df_step1[df_step1['색상'] == clr]
                elif cat == "입고":
                    s_list = sorted(list(set(df_step1['규격'])))
                    spc    = st.selectbox("2.규격", s_list) if s_list else None
                    final  = df_step1[df_step1['규격'] == spc] if spc else df_step1
                else:
                    spc      = st.selectbox("2.규격", sorted(list(set(df_step1['규격']))))
                    df_step2 = df_step1[df_step1['규격'] == spc]
                    if not df_step2.empty:
                        clr      = st.selectbox("3.색상", sorted(list(set(df_step2['색상']))))
                        df_step3 = df_step2[df_step2['색상'] == clr]
                        if not df_step3.empty:
                            typ   = st.selectbox("4.타입", sorted(list(set(df_step3['타입']))))
                            final = df_step3[df_step3['타입'] == typ]

                if not final.empty:
                    item_info = final.iloc[0]; sel_code = item_info['코드']
                    st.success(f"선택: {sel_code}")
                    if cat == "재고실사" and not df_inventory.empty:
                        inv_rows = df_inventory[df_inventory['코드'].astype(str) == str(sel_code)]
                        sys_q = inv_rows['현재고'].apply(safe_float).sum()
                        st.info(f"전산 재고(통합): {sys_q:,.1f}")
                else: item_info = None

        qty_in  = st.number_input("수량") if cat != "재고실사" else 0.0
        note_in = st.text_input("비고")
        if cat == "재고실사":
            real   = st.number_input("실사값(통합)", value=float(sys_q))
            qty_in = real - sys_q
            note_in = f"[실사] {note_in}"

        if st.button("저장"):
            if item_info is None:
                st.error("🚨 품목이 선택되지 않았습니다.")
            elif qty_in == 0 and cat != "재고실사":
                st.warning("⚠️ 수량이 0입니다. 확인해주세요.")
            elif sheet_logs:
                try:
                    sheet_logs.append_row([
                        date.strftime('%Y-%m-%d'), time_str, factory, cat,
                        sel_code, item_info['품목명'], item_info['규격'],
                        item_info['타입'], item_info['색상'], qty_in, note_in, "-", prod_line
                    ])
                    chg = qty_in  # 입고/생산/실사 모두 qty_in 그대로 (양수=증가, 음수=감소)
                    update_inventory(factory, sel_code, chg, item_info['품목명'], item_info['규격'],
                                     item_info['타입'], item_info['색상'], item_info.get('단위', '-'))

                    if cat == "생산" and not df_bom.empty:
                        sel_type = item_info['타입']
                        bom_targets = df_bom[df_bom['제품코드'].astype(str) == str(sel_code)]
                        if '타입' in df_bom.columns:
                            bom_targets = bom_targets[bom_targets['타입'].astype(str) == str(sel_type)]
                        bom_targets = bom_targets.drop_duplicates(subset=['자재코드'])
                        for _, r in bom_targets.iterrows():
                            req = qty_in * safe_float(r['소요량'])
                            update_inventory(factory, r['자재코드'], -req)  # 원자재 차감
                            time.sleep(0.3)
                            sheet_logs.append_row([
                                date.strftime('%Y-%m-%d'), time_str, factory, "사용(Auto)",
                                r['자재코드'], "System", "-", "-", "-", -req,
                                f"{sel_code} 생산", "-", prod_line
                            ])
                    st.cache_data.clear(); st.success("✅ 저장 완료"); st.rerun()
                except Exception as e: st.error(f"오류: {e}")

    st.title(f"📦 재고/생산 관리 ({factory})")
    t1, t2, t3, t4, t5 = st.tabs(["🏭 생산 이력", "📥 원자재 입고 이력", "📦 재고 현황", "📜 전체 로그", "🔩 BOM"])

    with t1:
        st.subheader("🔍 생산 이력 관리 (조회 및 수정/삭제)")
        if df_logs.empty:
            st.info("로그 데이터가 없습니다.")
        else:
            df_prod_log = df_logs[df_logs['구분'] == '생산'].copy()
            df_prod_log['No'] = df_prod_log.index + 2
            col_names = list(df_prod_log.columns)
            if len(col_names) >= 13: col_names[12] = '라인'
            df_prod_log.columns = col_names
            if '라인' not in df_prod_log.columns: df_prod_log['라인'] = "-"
            for col in ['코드', '품목명', '라인', '타입']:
                if col in df_prod_log.columns: df_prod_log[col] = df_prod_log[col].astype(str)

            with st.expander("🔎 검색 필터", expanded=True):
                c_s1, c_s2, c_s3, c_s4 = st.columns(4)
                min_dt    = pd.to_datetime(df_prod_log['날짜']).min().date() if not df_prod_log.empty else datetime.date.today()
                sch_date  = c_s1.date_input("날짜 범위", [min_dt, datetime.date.today()], key="p_date")
                all_lines = ["전체"] + sorted(df_prod_log['라인'].unique().tolist())
                sch_line  = c_s2.selectbox("라인 선택", all_lines)
                sch_code  = c_s3.text_input("품목 코드/명 검색", key="p_txt")
                sch_fac   = c_s4.selectbox("공장 필터", ["전체", "1공장", "2공장"])

            df_res = df_prod_log.copy()
            if len(sch_date) == 2:
                s_d, e_d = sch_date
                df_res['날짜'] = pd.to_datetime(df_res['날짜'])
                df_res = df_res[(df_res['날짜'].dt.date >= s_d) & (df_res['날짜'].dt.date <= e_d)]
                df_res['날짜'] = df_res['날짜'].dt.strftime('%Y-%m-%d')
            if sch_line != "전체": df_res = df_res[df_res['라인'] == sch_line]
            if sch_code: df_res = df_res[df_res['코드'].str.contains(sch_code, case=False) | df_res['품목명'].str.contains(sch_code, case=False)]
            if sch_fac != "전체": df_res = df_res[df_res['공장'] == sch_fac]

            st.write(f"📋 검색 결과: {len(df_res)}건")
            disp_cols  = ['No', '날짜', '시간', '공장', '라인', '코드', '품목명', '타입', '수량', '비고']
            final_cols = [c for c in disp_cols if c in df_res.columns]
            st.dataframe(df_res[final_cols].sort_values(['날짜', '시간'], ascending=False), use_container_width=True, hide_index=True)

            st.markdown("### 🛠️ 기록 수정 및 삭제")
            df_for_select  = df_res.sort_values(['날짜', '시간'], ascending=False)
            delete_options = {row['No']: f"No.{row['No']} | {row['날짜']} {row['품목명']} ({row['수량']}kg)" for _, row in df_for_select.iterrows()}
            if delete_options:
                sel_target_id = st.selectbox("관리할 기록 선택", list(delete_options.keys()), format_func=lambda x: delete_options[x])
                col_act1, col_act2 = st.columns(2)

                with col_act1:
                    if st.button("🗑️ 선택한 기록 삭제 (원자재 재고 복구)", type="primary"):
                        target_row = df_prod_log[df_prod_log['No'] == sel_target_id].iloc[0]
                        del_date = target_row['날짜']; del_time = target_row['시간']
                        del_fac  = target_row['공장']; del_code = target_row['코드']
                        del_qty  = safe_float(target_row['수량'])

                        # 제품 재고 복구 (생산량만큼 차감)
                        update_inventory(del_fac, del_code, -del_qty)

                        # 연동된 사용(Auto) 로그 찾아서 원자재 재고 복구 (실시간 재조회)
                        try:
                            live_all = sheet_logs.get_all_records()
                            rows_to_delete = [sel_target_id]
                            for idx, r in enumerate(live_all):
                                if (str(r.get('날짜','')) == str(del_date) and
                                    str(r.get('시간','')) == str(del_time) and
                                    str(r.get('구분','')) == '사용(Auto)' and
                                    str(del_code) in str(r.get('비고',''))):
                                    mat_qty = safe_float(r.get('수량', 0))
                                    update_inventory(del_fac, str(r.get('코드','')), -mat_qty)
                                    rows_to_delete.append(idx + 2)
                            rows_to_delete = sorted(list(set(rows_to_delete)), reverse=True)
                            for r_idx in rows_to_delete:
                                sheet_logs.delete_rows(int(r_idx))
                                time.sleep(0.3)
                            st.success("✅ 삭제 및 재고 복구 완료!")
                            time.sleep(1); st.cache_data.clear(); st.rerun()
                        except Exception as e: st.error(f"오류: {e}")

                with col_act2:
                    if st.button("✏️ 선택한 기록 수정하기"):
                        st.session_state["edit_mode"] = True

                if st.session_state["edit_mode"]:
                    st.info("💡 수정 시 기존 기록은 삭제되고 새로운 내용으로 다시 등록됩니다.")
                    target_row_edit = df_prod_log[df_prod_log['No'] == sel_target_id].iloc[0]
                    with st.form("edit_form"):
                        e_date = st.date_input("날짜", pd.to_datetime(target_row_edit['날짜']))
                        e_line = st.selectbox("라인", all_lines,
                                              index=all_lines.index(target_row_edit['라인']) if target_row_edit['라인'] in all_lines else 0)
                        e_qty  = st.number_input("수량 (kg)", value=float(target_row_edit['수량']))
                        e_note = st.text_input("비고", value=str(target_row_edit['비고']))

                        if st.form_submit_button("✅ 수정사항 저장"):
                            old_date = target_row_edit['날짜']; old_time = target_row_edit['시간']
                            old_fac  = target_row_edit['공장']; old_code = target_row_edit['코드']
                            old_qty  = safe_float(target_row_edit['수량'])

                            # 기존 제품 재고 취소
                            update_inventory(old_fac, old_code, -old_qty)

                            # 기존 원자재 사용 복구 (실시간 재조회)
                            try:
                                live_all_edit = sheet_logs.get_all_records()
                                rows_to_del_edit = [sel_target_id]
                                for idx, r in enumerate(live_all_edit):
                                    if (str(r.get('날짜','')) == str(old_date) and
                                        str(r.get('시간','')) == str(old_time) and
                                        str(r.get('구분','')) == '사용(Auto)' and
                                        str(old_code) in str(r.get('비고',''))):
                                        mat_qty = safe_float(r.get('수량', 0))
                                        update_inventory(old_fac, str(r.get('코드','')), -mat_qty)
                                        rows_to_del_edit.append(idx + 2)
                                rows_to_del_edit = sorted(list(set(rows_to_del_edit)), reverse=True)
                                for r_idx in rows_to_del_edit:
                                    sheet_logs.delete_rows(int(r_idx))
                                    time.sleep(0.3)
                            except Exception as e:
                                st.error(f"기존 기록 삭제 오류: {e}")

                            # 새 기록 등록
                            new_time_str = datetime.datetime.now().strftime("%H:%M:%S")
                            sheet_logs.append_row([
                                e_date.strftime('%Y-%m-%d'), new_time_str, old_fac, "생산",
                                old_code, target_row_edit['품목명'], target_row_edit.get('규격', ''),
                                target_row_edit['타입'], target_row_edit.get('색상', ''),
                                e_qty, e_note, "-", e_line
                            ])
                            update_inventory(old_fac, old_code, e_qty)

                            if not df_bom.empty:
                                sel_type    = target_row_edit['타입']
                                bom_targets = df_bom[df_bom['제품코드'].astype(str) == str(old_code)]
                                if '타입' in df_bom.columns:
                                    bom_targets = bom_targets[bom_targets['타입'].astype(str) == str(sel_type)]
                                bom_targets = bom_targets.drop_duplicates(subset=['자재코드'])
                                for _, r in bom_targets.iterrows():
                                    req = e_qty * safe_float(r['소요량'])
                                    update_inventory(old_fac, r['자재코드'], -req)
                                    time.sleep(0.3)
                                    sheet_logs.append_row([
                                        e_date.strftime('%Y-%m-%d'), new_time_str, old_fac, "사용(Auto)",
                                        r['자재코드'], "System", "-", "-", "-", -req,
                                        f"{old_code} 생산", "-", e_line
                                    ])

                            st.session_state["edit_mode"] = False
                            st.success("✅ 수정 완료!")
                            time.sleep(1); st.cache_data.clear(); st.rerun()

    with t2:
        st.subheader("📥 원자재 입고 이력 조회 및 취소")
        if df_logs.empty:
            st.info("데이터가 없습니다.")
        else:
            df_receipt_log = df_logs[df_logs['구분'] == '입고'].copy()
            df_receipt_log['No'] = df_receipt_log.index + 2

            with st.expander("🔎 입고 내역 검색", expanded=True):
                c_r1, c_r2 = st.columns(2)
                min_dt_r  = pd.to_datetime(df_receipt_log['날짜']).min().date() if not df_receipt_log.empty else datetime.date.today()
                sch_date_r = c_r1.date_input("날짜 범위", [min_dt_r, datetime.date.today()], key="r_date")
                sch_txt_r  = c_r2.text_input("품목 검색", key="r_txt")

            df_res_r = df_receipt_log.copy()
            if len(sch_date_r) == 2:
                s_d, e_d = sch_date_r
                df_res_r['날짜'] = pd.to_datetime(df_res_r['날짜'])
                df_res_r = df_res_r[(df_res_r['날짜'].dt.date >= s_d) & (df_res_r['날짜'].dt.date <= e_d)]
                df_res_r['날짜'] = df_res_r['날짜'].dt.strftime('%Y-%m-%d')
            if sch_txt_r:
                df_res_r = df_res_r[
                    df_res_r['코드'].astype(str).str.contains(sch_txt_r, case=False) |
                    df_res_r['품목명'].astype(str).str.contains(sch_txt_r, case=False)
                ]

            disp_cols_r = [c for c in ['No', '날짜', '시간', '공장', '코드', '품목명', '규격', '수량', '비고'] if c in df_res_r.columns]
            st.dataframe(df_res_r[disp_cols_r].sort_values(['날짜', '시간'], ascending=False), use_container_width=True, hide_index=True)

            st.markdown("### 🗑️ 잘못된 입고 기록 삭제")
            del_opts_r = {row['No']: f"No.{row['No']} | {row['날짜']} {row['품목명']} ({row['수량']}kg)" for _, row in df_res_r.iterrows()}
            if del_opts_r:
                sel_del_id_r = st.selectbox("삭제할 기록 선택", list(del_opts_r.keys()), format_func=lambda x: del_opts_r[x], key="sel_del_r")
                if st.button("❌ 입고 기록 삭제 (재고 차감)", type="primary"):
                    target_row_r = df_receipt_log[df_receipt_log['No'] == sel_del_id_r].iloc[0]
                    update_inventory(target_row_r['공장'], target_row_r['코드'], -safe_float(target_row_r['수량']))
                    sheet_logs.delete_rows(int(sel_del_id_r))
                    st.success("✅ 삭제 완료!"); time.sleep(1); st.cache_data.clear(); st.rerun()

    with t3:
        if not df_inventory.empty:
            df_v = df_inventory.copy()
            # 구분 컬럼 매핑
            if not df_items.empty:
                cmap = df_items.drop_duplicates('코드').set_index('코드')['구분'].to_dict()
                df_v['구분'] = df_v['코드'].map(cmap).fillna('-')
            # 현재고 숫자 변환
            if '현재고' in df_v.columns:
                df_v['현재고'] = pd.to_numeric(df_v['현재고'], errors='coerce').fillna(0)

            inv_c1, inv_c2, inv_c3 = st.columns(3)
            fac_f   = inv_c1.radio("공장", ["전체", "1공장", "2공장"], horizontal=True)
            cat_f   = inv_c2.radio("품목", ["전체", "제품", "반제품", "원자재"], horizontal=True)
            low_stk = inv_c3.checkbox("⚠️ 저재고만 보기", value=False)
            inv_search = st.text_input("🔍 코드/품목명 검색", key="inv_search")

            if fac_f != "전체": df_v = df_v[df_v['공장'] == fac_f]
            if cat_f != "전체":
                if cat_f == "제품": df_v = df_v[df_v['구분'].isin(['제품', '완제품'])]
                else: df_v = df_v[df_v['구분'] == cat_f]
            if inv_search:
                df_v = df_v[
                    df_v['코드'].astype(str).str.contains(inv_search, case=False, na=False) |
                    df_v['품목명'].astype(str).str.contains(inv_search, case=False, na=False)
                ]
            if low_stk and '현재고' in df_v.columns:
                df_v = df_v[df_v['현재고'] <= 0]

            # 재고 요약 지표
            if '현재고' in df_v.columns:
                m1, m2, m3 = st.columns(3)
                m1.metric("총 품목 수", f"{len(df_v)}개")
                neg_cnt  = len(df_v[df_v['현재고'] < 0])
                zero_cnt = len(df_v[df_v['현재고'] == 0])
                m2.metric("⚠️ 음수 재고", f"{neg_cnt}개",
                          delta="확인 필요" if neg_cnt > 0 else "정상",
                          delta_color="inverse" if neg_cnt > 0 else "off")
                m3.metric("📭 재고 소진", f"{zero_cnt}개")

            # 음수/0 재고 행 색상 강조
            def highlight_low(row):
                if '현재고' in row.index:
                    val = safe_float(row['현재고'])
                    if val < 0:  return ['background-color: #ffe0e0'] * len(row)
                    if val == 0: return ['background-color: #fff8d6'] * len(row)
                return [''] * len(row)

            st.dataframe(df_v.style.apply(highlight_low, axis=1), use_container_width=True)
        else: st.info("재고 데이터가 없습니다.")

    with t4: st.dataframe(df_logs, use_container_width=True)
    with t5: st.dataframe(df_bom, use_container_width=True)

# ==================== [2] 영업/출고 관리 ====================
elif menu == "영업/출고 관리":
    st.title("📑 영업 주문 및 출고 관리")
    if sheet_orders is None:
        st.error("'Orders' 시트가 없습니다.")
        st.info("사이드바의 🔄 새로고침 버튼을 눌러 재연결해주세요.")
    else:
        tab_o, tab_p, tab_prt, tab_out, tab_cancel = st.tabs([
            "📝 1. 주문 등록", "✏️ 2. 팔레트 수정/재구성",
            "🖨️ 3. 명세서/라벨 인쇄", "🚚 4. 출고 확정", "↩️ 5. 출고 취소(복구)"
        ])

        with tab_o:
            c1, c2 = st.columns([1, 2])
            with c1:
                st.subheader("주문 입력")
                od_dt  = st.date_input("주문일", datetime.datetime.now())
                cl_nm  = st.text_input("거래처명 (CUSTOMER)", placeholder="예: SHANGHAI YILIU")
                if not df_items.empty:
                    df_sale = df_items[df_items['구분'].isin(['제품', '완제품'])].copy()
                    df_sale['Disp'] = df_sale['코드'].astype(str) + " (" + df_sale['규격'].astype(str) + "/" + df_sale['색상'].astype(str) + "/" + df_sale['타입'].astype(str) + ")"
                    sel_it  = st.selectbox("품목 선택", df_sale['Disp'].unique())
                    row_it  = df_sale[df_sale['Disp'] == sel_it].iloc[0]
                    ord_q   = st.number_input("주문량(kg)", step=100.0)
                    ord_rem = st.text_input("📦 포장 단위 (REMARK)", value="BOX")
                    if st.button("🛒 장바구니 담기"):
                        st.session_state['cart'].append({
                            "코드": row_it['코드'], "품목명": row_it['품목명'], "규격": row_it['규격'],
                            "색상": row_it['색상'], "타입": row_it['타입'], "수량": ord_q, "비고": ord_rem
                        }); st.rerun()
            with c2:
                st.subheader("🛒 장바구니 목록")
                if st.session_state['cart']:
                    for i, it in enumerate(st.session_state['cart']):
                        ci1, ci2, ci3 = st.columns([4, 2, 1])
                        ci1.write(f"**{it['코드']}** ({it['품목명']})")
                        ci2.write(f"{it['수량']:,}kg / {it['비고']}")
                        if ci3.button("❌", key=f"cart_del_{i}"):
                            st.session_state['cart'].pop(i); st.rerun()
                    st.markdown("---")
                    max_pallet_kg = st.number_input("📦 팔레트당 최대 적재량 (kg)", min_value=100.0, value=1000.0, step=100.0)
                    col_btn1, col_btn2 = st.columns(2)
                    if col_btn1.button("🗑️ 장바구니 비우기"):
                        st.session_state['cart'] = []; st.rerun()
                    if col_btn2.button("✅ 최종 주문 확정", type="primary"):
                        oid  = "ORD-" + datetime.datetime.now().strftime("%y%m%d%H%M")
                        rows = []; plt_n = 1; cw = 0
                        for it in st.session_state['cart']:
                            rem = it['수량']
                            while rem > 0:
                                sp = max_pallet_kg - cw
                                if sp <= 0: plt_n += 1; cw = 0; sp = max_pallet_kg
                                load = min(rem, sp)
                                rows.append([oid, od_dt.strftime('%Y-%m-%d'), cl_nm, it['코드'], it['품목명'], load, plt_n, "준비", it['비고'], "", it['타입']])
                                cw += load; rem -= load
                        for r in rows: sheet_orders.append_row(r)
                        st.session_state['cart'] = []; st.cache_data.clear(); st.success("✅ 주문 저장 완료!"); st.rerun()
                else: st.info("장바구니가 비어있습니다.")

        with tab_p:
            st.subheader("✏️ 팔레트 수정 및 일괄 재구성")
            if not df_orders.empty and '상태' in df_orders.columns:
                pend = df_orders[df_orders['상태'] == '준비']
                if not pend.empty:
                    unique_ords = pend[['주문번호', '날짜', '거래처']].drop_duplicates().set_index('주문번호')
                    order_dict  = unique_ords.to_dict('index')
                    tgt = st.selectbox("수정할 주문 선택", pend['주문번호'].unique(),
                                       format_func=lambda x: f"{order_dict[x]['날짜']} | {order_dict[x]['거래처']} ({x})")

                    original_df = pend[pend['주문번호'] == tgt].copy()
                    original_df['Real_Index'] = range(len(original_df))
                    original_df['팔레트번호'] = pd.to_numeric(original_df['팔레트번호'], errors='coerce').fillna(999)
                    display_df = original_df.sort_values('팔레트번호')

                    st.write("▼ 현재 팔레트 구성")
                    st.dataframe(display_df[['팔레트번호', '코드', '품목명', '수량', '비고']], use_container_width=True, hide_index=True)

                    with st.expander("📦 팔레트 일괄 재구성 (Re-Split)", expanded=False):
                        st.warning("⚠️ 실행 시 기존 팔레트 번호와 수량이 재계산됩니다.")
                        new_max_kg = st.number_input("팔레트당 적재량 (kg)", min_value=100.0, value=1200.0, step=100.0, key="resplit_kg")
                        if st.button("🚀 재구성 실행"):
                            with st.spinner("팔레트 재계산 중..."):
                                combined = original_df.groupby(['코드', '품목명', '비고', '타입'])['수량'].sum().reset_index()
                                new_rows_data = []; plt_cnt = 1; current_w = 0
                                for _, r in combined.iterrows():
                                    rem = r['수량']
                                    while rem > 0:
                                        space = new_max_kg - current_w
                                        if space <= 0: plt_cnt += 1; current_w = 0; space = new_max_kg
                                        load = min(rem, space)
                                        new_rows_data.append([tgt, original_df.iloc[0]['날짜'], original_df.iloc[0]['거래처'], r['코드'], r['품목명'], load, plt_cnt, "준비", r['비고'], "", r['타입']])
                                        current_w += load; rem -= load
                                all_records = sheet_orders.get_all_records()
                                headers     = sheet_orders.row_values(1)
                                filtered    = [r for r in all_records if str(r['주문번호']) != str(tgt)]
                                sheet_orders.clear()
                                sheet_orders.update([headers] + [[r.get(h, "") for h in headers] for r in filtered] + new_rows_data)
                                st.success("✅ 재구성 완료!"); st.cache_data.clear(); time.sleep(1); st.rerun()

                    st.markdown("---")
                    c_mod1, c_mod2 = st.columns(2)
                    with c_mod1:
                        st.markdown("#### ➕ 품목 추가")
                        with st.form("add_form"):
                            new_code = st.selectbox("제품 코드", df_items['코드'].unique())
                            new_qty  = st.number_input("수량(kg)", min_value=0.0, step=10.0)
                            new_plt  = st.number_input("팔레트 번호", value=int(display_df['팔레트번호'].max()))
                            if st.form_submit_button("추가"):
                                sheet_orders.append_row([tgt, original_df.iloc[0]['날짜'], original_df.iloc[0]['거래처'], new_code, "", new_qty, new_plt, "준비", "BOX", "", ""])
                                st.success("✅ 추가됨"); st.cache_data.clear(); st.rerun()

                    with c_mod2:
                        st.markdown("#### 🛠️ 개별 수정")
                        edit_opts = {r['Real_Index']: f"PLT {r['팔레트번호']} | {r['코드']} ({r['수량']}kg)" for _, r in display_df.iterrows()}
                        sel_idx = st.selectbox("수정할 라인", list(edit_opts.keys()), format_func=lambda x: edit_opts[x])
                        target  = original_df[original_df['Real_Index'] == sel_idx].iloc[0]
                        with st.form("edit_line_form"):
                            ed_qty = st.number_input("수량", value=float(target['수량']))
                            ed_plt = st.number_input("팔레트", value=int(target['팔레트번호']))
                            if st.form_submit_button("💾 저장"):
                                all_vals = sheet_orders.get_all_records()
                                headers  = sheet_orders.row_values(1)
                                updated  = []; row_count = 0
                                for r in all_vals:
                                    if str(r['주문번호']) == str(tgt):
                                        if row_count == sel_idx: r['수량'] = ed_qty; r['팔레트번호'] = ed_plt
                                        row_count += 1
                                    updated.append([r.get(h, "") for h in headers])
                                sheet_orders.clear(); sheet_orders.update([headers] + updated)
                                st.success("✅ 수정됨"); st.cache_data.clear(); st.rerun()
                else: st.info("준비 상태인 주문이 없습니다.")
            else: st.info("주문 데이터가 없습니다.")

        with tab_prt:
            st.subheader("🖨️ Packing List & Labels")
            if not df_orders.empty and '상태' in df_orders.columns:
                pend = df_orders[df_orders['상태'] == '준비']
                if not pend.empty:
                    unique_ords_prt = pend[['주문번호', '날짜', '거래처']].drop_duplicates().set_index('주문번호')
                    order_dict_prt  = unique_ords_prt.to_dict('index')
                    def format_ord_prt(oid):
                        info = order_dict_prt.get(oid)
                        return f"{info['날짜']} | {info['거래처']} ({oid})" if info else oid

                    tgt_p = st.selectbox("출력할 주문", pend['주문번호'].unique(), key='prt_sel', format_func=format_ord_prt)
                    dp = pend[pend['주문번호'] == tgt_p].copy()
                    dp['팔레트번호'] = pd.to_numeric(dp['팔레트번호'], errors='coerce').fillna(999)
                    dp = dp.sort_values('팔레트번호')

                    if not dp.empty:
                        cli       = dp.iloc[0]['거래처']
                        ex_date   = dp.iloc[0]['날짜']
                        ship_date = datetime.datetime.now().strftime("%Y-%m-%d")

                        st.markdown("#### ✏️ 출력용 제품명 변경")
                        unique_codes = sorted(dp['코드'].unique())
                        saved_map    = dict(zip(df_mapping['Code'].astype(str), df_mapping['Print_Name'].astype(str))) if not df_mapping.empty else {}
                        current_map_data = [{"Internal": str(c), "Customer_Print_Name": saved_map.get(str(c), str(c))} for c in unique_codes]
                        edited_map = st.data_editor(pd.DataFrame(current_map_data), use_container_width=True, hide_index=True)
                        code_map   = dict(zip(edited_map['Internal'], edited_map['Customer_Print_Name']))

                        if st.button("💾 이름 영구 저장"):
                            ws_map = get_sheet(doc, "Print_Mapping", ["Code", "Print_Name"])
                            db_map = {str(r['Code']): str(r['Print_Name']) for r in df_mapping.to_dict('records')}
                            db_map.update(code_map)
                            rows = [["Code", "Print_Name"]] + [[k, v] for k, v in db_map.items()]
                            ws_map.clear(); ws_map.update(rows); st.success("✅ 저장됨"); st.cache_data.clear(); st.rerun()

                        sub_t1, sub_t2, sub_t3 = st.tabs(["📄 명세서", "🔷 다이아몬드 라벨", "📑 표준 라벨"])

                        # --- 명세서 ---
                        with sub_t1:
                            pl_rows = ""; tot_q = 0; tot_plt = dp['팔레트번호'].nunique()
                            for plt_num, group in dp.groupby('팔레트번호'):
                                g_len = len(group); is_first = True
                                for _, r in group.iterrows():
                                    shp          = get_shape(r['코드'], df_items)
                                    display_name = code_map.get(str(r['코드']), str(r['코드']))
                                    pl_rows += "<tr>"
                                    if is_first: pl_rows += f"<td rowspan='{g_len}' align='center'>{int(plt_num)}</td>"
                                    pl_rows += f"<td>{display_name}</td><td align='right'>{r['수량']:,.0f}</td><td align='center'>-</td><td align='center'>{shp}</td><td align='center'>-</td><td align='center'>{r['비고']}</td></tr>"
                                    is_first = False; tot_q += r['수량']

                            html_pl = f"""
                            <div style='font-family:Arial;padding:10px;'>
                            <h2 style='text-align:center;'>PACKING LIST</h2>
                            <p><b>CUSTOMER:</b> {cli} &nbsp;&nbsp; <b>DATE:</b> {ship_date} &nbsp;&nbsp; <b>TOTAL PLT:</b> {tot_plt} &nbsp;&nbsp; <b>TOTAL QTY:</b> {tot_q:,.0f} KG</p>
                            <table border='1' style='width:100%;border-collapse:collapse;font-size:13px;'>
                            <thead><tr style='background:#ddd;'><th>PLT</th><th>ITEM</th><th>QTY(KG)</th><th>COLOR</th><th>SHAPE</th><th>LOT#</th><th>REMARK</th></tr></thead>
                            <tbody>{pl_rows}</tbody>
                            </table></div>"""
                            st.components.v1.html(html_pl, height=450, scrolling=True)
                            st.components.v1.html(create_print_button(html_pl, "PackingList", "landscape"), height=60)

                        # --- 다이아몬드 라벨 (버그수정: 미구현 → 구현) ---
                        with sub_t2:
                            st.markdown("##### 🔷 팔레트별 다이아몬드 라벨")
                            label_html = ""
                            for plt_num, group in dp.groupby('팔레트번호'):
                                items_in_plt = []
                                for _, r in group.iterrows():
                                    display_name = code_map.get(str(r['코드']), str(r['코드']))
                                    shp = get_shape(r['코드'], df_items)
                                    items_in_plt.append(f"{display_name} / {shp} / {r['수량']:,.0f}KG")
                                item_str = "<br>".join(items_in_plt)
                                label_html += f"""
                                <div class='page-break' style='width:190mm;height:270mm;display:flex;align-items:center;justify-content:center;'>
                                <div style='width:160mm;height:160mm;border:4px solid black;transform:rotate(45deg);display:flex;align-items:center;justify-content:center;'>
                                <div style='transform:rotate(-45deg);text-align:center;padding:10px;'>
                                    <div style='font-size:18pt;font-weight:bold;'>{cli}</div>
                                    <div style='font-size:13pt;margin:8px 0;'>{item_str}</div>
                                    <div style='font-size:14pt;font-weight:bold;'>PLT No. {int(plt_num)}</div>
                                    <div style='font-size:11pt;color:#555;'>{ship_date}</div>
                                </div>
                                </div></div>"""

                            st.components.v1.html(label_html, height=400, scrolling=True)
                            st.components.v1.html(create_print_button(label_html, "Diamond_Label", "portrait"), height=60)

                        # --- 표준 라벨 (버그수정: 미구현 → 구현) ---
                        with sub_t3:
                            st.markdown("##### 📑 팔레트별 표준 라벨")
                            std_label_html = ""
                            for plt_num, group in dp.groupby('팔레트번호'):
                                for _, r in group.iterrows():
                                    display_name = code_map.get(str(r['코드']), str(r['코드']))
                                    shp = get_shape(r['코드'], df_items)
                                    std_label_html += f"""
                                    <div class='page-break' style='width:200mm;height:140mm;border:2px solid black;padding:12px;font-family:Arial;box-sizing:border-box;'>
                                    <table style='width:100%;height:100%;border-collapse:collapse;'>
                                    <tr><td colspan='4' style='font-size:20pt;font-weight:bold;border-bottom:2px solid black;padding:6px;'>SHIPPING LABEL</td></tr>
                                    <tr>
                                        <td style='font-size:11pt;color:#555;padding:4px;width:25%;'>CUSTOMER</td>
                                        <td colspan='3' style='font-size:14pt;font-weight:bold;padding:4px;'>{cli}</td>
                                    </tr>
                                    <tr>
                                        <td style='font-size:11pt;color:#555;padding:4px;'>ITEM</td>
                                        <td colspan='3' style='font-size:13pt;font-weight:bold;padding:4px;'>{display_name}</td>
                                    </tr>
                                    <tr>
                                        <td style='font-size:11pt;color:#555;padding:4px;'>SHAPE</td>
                                        <td style='font-size:13pt;padding:4px;'>{shp}</td>
                                        <td style='font-size:11pt;color:#555;padding:4px;'>QTY</td>
                                        <td style='font-size:15pt;font-weight:bold;padding:4px;'>{r['수량']:,.0f} KG</td>
                                    </tr>
                                    <tr>
                                        <td style='font-size:11pt;color:#555;padding:4px;'>PLT No.</td>
                                        <td style='font-size:16pt;font-weight:bold;padding:4px;'>{int(plt_num)}</td>
                                        <td style='font-size:11pt;color:#555;padding:4px;'>DATE</td>
                                        <td style='font-size:12pt;padding:4px;'>{ship_date}</td>
                                    </tr>
                                    <tr>
                                        <td style='font-size:11pt;color:#555;padding:4px;'>REMARK</td>
                                        <td colspan='3' style='font-size:12pt;padding:4px;'>{r['비고']}</td>
                                    </tr>
                                    </table></div>"""

                            st.components.v1.html(std_label_html, height=400, scrolling=True)
                            st.components.v1.html(create_print_button(std_label_html, "Standard_Label", "landscape"), height=60)
                else: st.info("준비 상태인 주문이 없습니다.")
            else: st.info("주문 데이터가 없습니다.")

        with tab_out:
            st.subheader("🚚 출고 확정 및 재고 차감")
            if not df_orders.empty:
                pend = df_orders[df_orders['상태'] == '준비']
                if not pend.empty:
                    unique_ords_out = pend[['주문번호', '날짜', '거래처']].drop_duplicates().set_index('주문번호')
                    tgt_out = st.selectbox("출고할 주문 선택", pend['주문번호'].unique(),
                                           format_func=lambda x: f"{unique_ords_out.loc[x]['날짜']} | {unique_ords_out.loc[x]['거래처']} ({x})")
                    d_out = pend[pend['주문번호'] == tgt_out]
                    st.dataframe(d_out[['코드', '품목명', '수량', '팔레트번호']], use_container_width=True)
                    total_out = d_out['수량'].sum()
                    st.info(f"총 출고량: {total_out:,.0f} kg")

                    if st.button("🚀 출고 확정", type="primary"):
                        for _, row in d_out.iterrows():
                            qty_out = safe_float(row['수량'])
                            update_inventory(factory, row['코드'], -qty_out)
                            sheet_logs.append_row([
                                datetime.date.today().strftime('%Y-%m-%d'), time_str, factory, "출고",
                                row['코드'], row['품목명'], "-", row.get('타입', '-'), "-",
                                -qty_out, f"주문출고({tgt_out})", row['거래처'], "-"
                            ])
                            time.sleep(0.2)
                        all_rec = sheet_orders.get_all_records(); hd = sheet_orders.row_values(1)
                        upd = [hd] + [
                            [(r['상태'] if h != '상태' else '완료') if r['주문번호'] == tgt_out else r.get(h, "") for h in hd]
                            for r in all_rec
                        ]
                        sheet_orders.clear(); sheet_orders.update(upd)
                        st.success("✅ 출고 완료"); st.cache_data.clear(); st.rerun()
                else: st.info("준비 상태인 주문이 없습니다.")
            else: st.info("주문 데이터가 없습니다.")

        # ✅ 버그수정: 출고 취소 탭 구현
        with tab_cancel:
            st.subheader("↩️ 출고 취소 및 재고 복구")
            st.warning("⚠️ 출고 취소 시 해당 주문의 제품 재고가 복구되고, 주문 상태가 '준비'로 돌아갑니다.")
            if not df_orders.empty and '상태' in df_orders.columns:
                done = df_orders[df_orders['상태'] == '완료']
                if not done.empty:
                    unique_done = done[['주문번호', '날짜', '거래처']].drop_duplicates().set_index('주문번호')
                    done_dict   = unique_done.to_dict('index')
                    tgt_cancel  = st.selectbox(
                        "취소할 주문 선택", done['주문번호'].unique(),
                        format_func=lambda x: f"{done_dict[x]['날짜']} | {done_dict[x]['거래처']} ({x})"
                    )
                    d_cancel = done[done['주문번호'] == tgt_cancel]
                    st.dataframe(d_cancel[['코드', '품목명', '수량', '팔레트번호']], use_container_width=True)
                    total_cancel = d_cancel['수량'].sum()
                    st.info(f"복구 예정 재고량: {total_cancel:,.0f} kg")

                    if st.button("↩️ 출고 취소 실행 (재고 복구)", type="primary"):
                        # 1. 재고 복구
                        for _, row in d_cancel.iterrows():
                            update_inventory(factory, row['코드'], safe_float(row['수량']))
                            time.sleep(0.2)

                        # 2. 출고 로그에서 해당 주문 로그 제거 (실시간 데이터 재조회)
                        try:
                            live_logs = sheet_logs.get_all_records()
                            cancel_keyword = str(tgt_cancel)
                            del_indices = []
                            for idx, r in enumerate(live_logs):
                                if str(r.get('구분', '')) == '출고' and cancel_keyword in str(r.get('비고', '')):
                                    del_indices.append(idx + 2)  # 헤더 행 +1, 0-index → 1-index +1
                            del_indices.sort(reverse=True)
                            for r_idx in del_indices:
                                sheet_logs.delete_rows(int(r_idx))
                                time.sleep(0.3)
                        except Exception as e:
                            st.warning(f"출고 로그 삭제 중 오류 (재고는 복구됨): {e}")

                        # 3. 주문 상태를 '완료' → '준비'로 복구
                        all_rec = sheet_orders.get_all_records(); hd = sheet_orders.row_values(1)
                        upd = [hd] + [
                            [(r['상태'] if h != '상태' else '준비') if r['주문번호'] == tgt_cancel else r.get(h, "") for h in hd]
                            for r in all_rec
                        ]
                        sheet_orders.clear(); sheet_orders.update(upd)
                        st.success("✅ 출고 취소 및 재고 복구 완료!")
                        st.cache_data.clear(); time.sleep(1); st.rerun()
                else:
                    st.info("완료된 출고 주문이 없습니다.")
            else:
                st.info("주문 데이터가 없습니다.")

# ==================== [3] 현장 작업 (LOT 입력) ====================
elif menu == "🏭 현장 작업 (LOT 입력)":
    try:
        st.title("🏭 현장 작업 입력 (간편 LOT)")
        st.info("💡 현장 작업자용 간편 입력 화면입니다.")

        c1, c2, c3 = st.columns(3)
        lot_date    = c1.date_input("작업일", datetime.date.today(), key="lot_date_w")
        lot_factory = c2.selectbox("공장", ["1공장", "2공장"], key="lot_factory_w")
        lot_cat     = c3.selectbox("구분", ["생산", "입고"], key="lot_cat_w")

        c4, c5 = st.columns(2)
        if lot_cat == "생산":
            if lot_factory == "1공장": line_opts = [f"압출{i}호" for i in range(1, 6)] + ["기타"]
            else:                       line_opts = [f"압출{i}호" for i in range(1, 7)] + [f"컷팅{i}호" for i in range(1, 11)] + ["기타"]
            lot_line = c4.selectbox("설비 라인", line_opts, key="lot_line_w")
        else:
            lot_line = "-"

        lot_row = None
        if df_items.empty:
            st.warning("⚠️ 품목 데이터가 없습니다. 새로고침을 눌러주세요.")
        else:
            df_lot_items = df_items.copy()
            # 구분 컬럼 유무 확인 후 필터
            if '구분' in df_lot_items.columns:
                if lot_cat == "생산":
                    df_lot_items = df_lot_items[df_lot_items['구분'].isin(['제품', '완제품', '반제품'])]
                else:
                    df_lot_items = df_lot_items[df_lot_items['구분'] == '원자재']
            # 필터 후 비어있으면 전체 표시
            if df_lot_items.empty:
                df_lot_items = df_items.copy()
                st.warning("⚠️ 해당 구분 품목이 없어 전체 품목을 표시합니다.")

            cols_for_disp = ['코드','품목명','규격']
            for col in cols_for_disp:
                if col not in df_lot_items.columns:
                    df_lot_items[col] = ''
            df_lot_items['Disp'] = (df_lot_items['코드'].astype(str) + " | " +
                                    df_lot_items['품목명'].astype(str) + " (" +
                                    df_lot_items['규격'].astype(str) + ")")
            disp_opts = df_lot_items['Disp'].unique().tolist()
            lot_sel = c5.selectbox("품목 선택", disp_opts, key="lot_item_w")
            matched = df_lot_items[df_lot_items['Disp'] == lot_sel]
            if not matched.empty:
                lot_row = matched.iloc[0]

        c6, c7 = st.columns(2)
        lot_qty  = c6.number_input("수량 (kg)", min_value=0.0, step=10.0, key="lot_qty_w")
        lot_note = c7.text_input("비고 (LOT번호 등)", key="lot_note_w")

        if lot_row is not None:
            st.success(f"선택: **{lot_row.get('코드','')}** | {lot_row.get('품목명','')} | {lot_row.get('규격','')} | {lot_row.get('타입','')} | {lot_row.get('색상','')}")

        if st.button("✅ 작업 기록 저장", type="primary", key="lot_save_btn"):
            if lot_row is None:
                st.error("🚨 품목을 선택해주세요.")
            elif lot_qty <= 0:
                st.error("🚨 수량을 입력해주세요.")
            elif not sheet_logs:
                st.error("🚨 시트 연결 오류입니다. 새로고침 후 다시 시도하세요.")
            else:
                try:
                    now_str = datetime.datetime.now().strftime("%H:%M:%S")
                    sheet_logs.append_row([
                        lot_date.strftime('%Y-%m-%d'), now_str, lot_factory, lot_cat,
                        lot_row.get('코드',''), lot_row.get('품목명',''), lot_row.get('규격','-'),
                        lot_row.get('타입','-'), lot_row.get('색상','-'),
                        lot_qty, lot_note, "-", lot_line
                    ])
                    update_inventory(lot_factory, lot_row.get('코드',''), lot_qty,
                                     lot_row.get('품목명',''), lot_row.get('규격','-'),
                                     lot_row.get('타입','-'), lot_row.get('색상','-'))
                    if lot_cat == "생산" and not df_bom.empty:
                        bom_t = df_bom[df_bom['제품코드'].astype(str) == str(lot_row.get('코드',''))]
                        if '타입' in df_bom.columns:
                            bom_t = bom_t[bom_t['타입'].astype(str) == str(lot_row.get('타입',''))]
                        bom_t = bom_t.drop_duplicates(subset=['자재코드'])
                        for _, r in bom_t.iterrows():
                            req = lot_qty * safe_float(r['소요량'])
                            update_inventory(lot_factory, r['자재코드'], -req)
                            time.sleep(0.3)
                            sheet_logs.append_row([
                                lot_date.strftime('%Y-%m-%d'), now_str, lot_factory, "사용(Auto)",
                                r['자재코드'], "System", "-", "-", "-", -req,
                                f"{lot_row.get('코드','')} 생산", "-", lot_line
                            ])
                    st.cache_data.clear()
                    st.success(f"✅ [{lot_row.get('코드','')}] {lot_cat} {lot_qty:,.0f}kg 저장 완료!")
                    st.rerun()
                except Exception as e:
                    st.error(f"저장 오류: {e}")

        st.markdown("---")
        st.subheader(f"📋 오늘({datetime.date.today()}) 작업 현황")
        if not df_logs.empty:
            today_str    = datetime.date.today().strftime('%Y-%m-%d')
            df_today_lot = df_logs[
                (df_logs['날짜'].astype(str).str[:10] == today_str) &
                (df_logs['구분'].isin(['생산', '입고']))
            ].copy()
            if not df_today_lot.empty:
                safe_disp = [c for c in ['날짜', '시간', '공장', '구분', '코드', '품목명', '수량', '비고'] if c in df_today_lot.columns]
                st.dataframe(df_today_lot[safe_disp].sort_values('시간', ascending=False),
                             use_container_width=True, hide_index=True)
                prod_today = df_today_lot[df_today_lot['구분'] == '생산']['수량'].sum()
                st.metric("오늘 총 생산량", f"{prod_today:,.0f} kg")
            else:
                st.info("오늘 작업 기록이 없습니다.")
        else:
            st.info("데이터가 없습니다.")
    except Exception as _e:
        st.error(f"❌ 현장 작업 화면 오류: {_e}")
        st.info("사이드바의 🔄 새로고침 버튼을 눌러주세요.")

# ==================== [4] 이력/LOT 검색 ====================
elif menu == "🔍 이력/LOT 검색":
    try:
        st.title("🔍 이력 및 LOT 통합 검색")

        s1, s2, s3 = st.columns(3)
        srch_keyword = s1.text_input("🔑 키워드 (코드/품목명/비고)", placeholder="예: KA100, LOT-001", key="srch_kw")
        srch_type    = s2.multiselect("구분 필터", ["생산", "입고", "출고", "사용(Auto)", "재고실사"],
                                      default=["생산", "입고", "출고"], key="srch_type")
        srch_fac     = s3.radio("공장", ["전체", "1공장", "2공장"], horizontal=True, key="srch_fac")

        col_d1, col_d2 = st.columns(2)
        default_start = datetime.date.today() - datetime.timedelta(days=30)
        srch_start = col_d1.date_input("시작일", default_start, key="srch_start")
        srch_end   = col_d2.date_input("종료일", datetime.date.today(), key="srch_end")

        st.markdown("---")

        if df_logs.empty:
            st.warning("⚠️ 로그 데이터가 없습니다. 새로고침을 눌러주세요.")
        else:
            df_search = df_logs.copy()

            # 날짜 필터
            if '날짜' in df_search.columns:
                df_search['날짜_dt'] = pd.to_datetime(df_search['날짜'], errors='coerce')
                df_search = df_search[df_search['날짜_dt'].notna()]
                df_search = df_search[
                    (df_search['날짜_dt'].dt.date >= srch_start) &
                    (df_search['날짜_dt'].dt.date <= srch_end)
                ]
                df_search['날짜'] = df_search['날짜_dt'].dt.strftime('%Y-%m-%d')
                df_search = df_search.drop(columns=['날짜_dt'])

            # 구분 필터
            if srch_type and '구분' in df_search.columns:
                df_search = df_search[df_search['구분'].isin(srch_type)]

            # 공장 필터
            if srch_fac != "전체" and '공장' in df_search.columns:
                df_search = df_search[df_search['공장'] == srch_fac]

            # 키워드 필터
            if srch_keyword.strip():
                kw = srch_keyword.strip()
                mask = pd.Series([False] * len(df_search), index=df_search.index)
                for col in ['코드', '품목명', '비고']:
                    if col in df_search.columns:
                        mask = mask | df_search[col].astype(str).str.contains(kw, case=False, na=False)
                df_search = df_search[mask]

            st.write(f"📋 검색 결과: **{len(df_search)}건**")

            if not df_search.empty:
                safe_cols = [c for c in ['날짜', '시간', '공장', '구분', '코드', '품목명', '규격', '타입', '색상', '수량', '비고'] if c in df_search.columns]
                sort_cols = [c for c in ['날짜', '시간'] if c in df_search.columns]
                st.dataframe(
                    df_search[safe_cols].sort_values(sort_cols, ascending=False) if sort_cols else df_search[safe_cols],
                    use_container_width=True, hide_index=True
                )

                st.markdown("---")
                st.subheader("📊 검색 결과 요약")
                col_s1, col_s2, col_s3 = st.columns(3)
                if '구분' in df_search.columns and '수량' in df_search.columns:
                    prod_sum = df_search[df_search['구분'] == '생산']['수량'].sum()
                    out_sum  = abs(df_search[df_search['구분'] == '출고']['수량'].sum())
                    in_sum   = df_search[df_search['구분'] == '입고']['수량'].sum()
                else:
                    prod_sum = out_sum = in_sum = 0
                col_s1.metric("총 생산량", f"{prod_sum:,.0f} kg")
                col_s2.metric("총 출고량", f"{out_sum:,.0f} kg")
                col_s3.metric("총 입고량", f"{in_sum:,.0f} kg")

                grp_cols = [c for c in ['코드', '품목명', '구분'] if c in df_search.columns]
                if grp_cols and '수량' in df_search.columns:
                    df_agg = df_search.groupby(grp_cols)['수량'].sum().reset_index()
                    df_agg['수량'] = df_agg['수량'].round(2)
                    st.markdown("##### 품목 × 구분별 집계")
                    st.dataframe(df_agg.sort_values('수량', ascending=False), use_container_width=True, hide_index=True)
            else:
                st.info("조건에 맞는 결과가 없습니다.")
    except Exception as _e:
        st.error(f"❌ 이력 검색 화면 오류: {_e}")
        st.info("사이드바의 🔄 새로고침 버튼을 눌러주세요.")

# ==================== [5] 환경/폐수 일지 ====================
elif menu == "🌊 환경/폐수 일지":
    st.title("🌊 폐수배출시설 운영일지")
    tab_w1, tab_w2 = st.tabs(["📅 운영일지 작성", "📋 이력 조회"])

    with tab_w1:
        st.markdown("### 📅 월간 운영일지 작성")
        c_gen1, c_gen2, c_gen3 = st.columns(3)
        sel_year  = c_gen1.number_input("연도", 2024, 2030, datetime.date.today().year)
        sel_month = c_gen2.number_input("월", 1, 12, datetime.date.today().month)
        use_random = c_gen3.checkbox("랜덤 변주 적용 (±1%)", value=False)

        if st.button("📝 일지 내역 작성"):
            start_date = datetime.date(sel_year, sel_month, 1)
            if sel_month == 12: end_date = datetime.date(sel_year + 1, 1, 1) - datetime.timedelta(days=1)
            else: end_date = datetime.date(sel_year, sel_month + 1, 1) - datetime.timedelta(days=1)
            date_list = pd.date_range(start=start_date, end=end_date)
            generated_rows = []
            for d in date_list:
                d_date = d.date(); d_str = d.strftime('%Y-%m-%d')
                wk     = ["월","화","수","목","금","토","일"][d_date.weekday()]
                full_d = f"{d.strftime('%Y년 %m월 %d일')} {wk}요일"
                daily_prod = df_logs[(df_logs['날짜'] == d_str) & (df_logs['공장'] == '1공장') & (df_logs['구분'] == '생산')] if not df_logs.empty else pd.DataFrame()
                if not daily_prod.empty:
                    t_qty = daily_prod['수량'].sum()
                    res   = round(t_qty * 0.8)
                    tm    = "08:00~15:00" if d_date.weekday() == 5 else "08:00~08:00"
                    if use_random: res = round(res * random.uniform(0.99, 1.01))
                    generated_rows.append({"날짜": full_d, "대표자": "문성인", "환경기술인": "문주혁", "가동시간": tm,
                                           "플라스틱재생칩": 0, "합성수지": res, "안료": 0.2,
                                           "용수사용량": 2.16, "폐수발생량": 0, "위탁량": "", "기타": "전량 재이용"})
                else:
                    generated_rows.append({"날짜": full_d, "대표자": "", "환경기술인": "", "가동시간": "",
                                           "플라스틱재생칩": "", "합성수지": "", "안료": "",
                                           "용수사용량": "", "폐수발생량": "", "위탁량": "", "기타": ""})
            st.session_state['wastewater_preview'] = pd.DataFrame(generated_rows); st.rerun()

        if 'wastewater_preview' in st.session_state:
            edited_ww = st.data_editor(st.session_state['wastewater_preview'], num_rows="dynamic", use_container_width=True)
            if st.button("💾 일지 저장"):
                for _, r in edited_ww.iterrows(): sheet_wastewater.append_row(list(r.values))
                del st.session_state['wastewater_preview']
                st.success("✅ 저장됨"); st.cache_data.clear(); st.rerun()

    # ✅ 버그수정: 폐수 이력 조회 탭 구현
    with tab_w2:
        st.markdown("### 📋 폐수 운영일지 이력 조회")
        if not df_wastewater.empty:
            w2_c1, w2_c2 = st.columns(2)
            ww_min_date = datetime.date.today().replace(day=1)
            ww_date_range = w2_c1.date_input("조회 기간", [ww_min_date, datetime.date.today()], key="ww_date")
            ww_keyword = w2_c2.text_input("키워드 검색 (날짜, 담당자 등)", key="ww_kw")

            df_ww_view = df_wastewater.copy()

            # 날짜가 문자열 형태이므로 키워드로 필터
            if ww_keyword:
                df_ww_view = df_ww_view[df_ww_view.apply(lambda row: row.astype(str).str.contains(ww_keyword, case=False).any(), axis=1)]

            st.dataframe(df_ww_view, use_container_width=True, hide_index=True)
            st.metric("조회된 일지 수", f"{len(df_ww_view)}일")

            # 합성수지 사용량 차트
            if '합성수지' in df_ww_view.columns and '날짜' in df_ww_view.columns:
                try:
                    df_ww_chart = df_ww_view[df_ww_view['합성수지'].astype(str).str.strip() != ""].copy()
                    df_ww_chart['합성수지_n'] = pd.to_numeric(df_ww_chart['합성수지'], errors='coerce')
                    df_ww_chart = df_ww_chart.dropna(subset=['합성수지_n'])
                    if not df_ww_chart.empty:
                        ww_chart = alt.Chart(df_ww_chart).mark_line(point=True).encode(
                            x=alt.X('날짜:N', title='날짜'),
                            y=alt.Y('합성수지_n:Q', title='합성수지 사용량'),
                            tooltip=['날짜', '합성수지_n']
                        ).properties(height=250, title="합성수지 사용량 추이")
                        st.altair_chart(ww_chart, use_container_width=True)
                except Exception: pass
        else:
            st.info("저장된 폐수 일지가 없습니다.")

# ==================== [6] 주간 회의 & 개선사항 ====================
elif menu == "📋 주간 회의 & 개선사항":
    st.title("📋 현장 주간 회의 및 개선사항 관리")
    tab_m1, tab_m2, tab_m3 = st.tabs(["🚀 진행 중인 안건", "➕ 신규 안건 등록", "🔍 안건 이력 및 인쇄"])

    with tab_m1:
        mtg_fac_filter = st.radio("공장 필터", ["전체", "1공장", "2공장", "공통"], horizontal=True)
        if not df_meetings.empty:
            df_open = df_meetings[df_meetings['상태'] != '완료'].copy()
            if mtg_fac_filter != "전체": df_open = df_open[df_open['공장'] == mtg_fac_filter]
            if not df_open.empty:
                # ✅ 버그수정: 편집된 데이터(edited_mtg)를 실제로 시트에 반영
                edited_mtg = st.data_editor(
                    df_open[['ID', '작성일', '공장', '안건내용', '담당자', '상태', '비고']],
                    use_container_width=True, hide_index=True
                )
                if st.button("💾 변경사항 저장"):
                    all_rec = sheet_meetings.get_all_records()
                    hd      = sheet_meetings.row_values(1)
                    # edited_mtg의 ID를 기준으로 원본 데이터 업데이트
                    edit_dict = {str(r['ID']): r for _, r in edited_mtg.iterrows()}
                    updated_rows = []
                    for r in all_rec:
                        rid = str(r.get('ID', ''))
                        if rid in edit_dict:
                            er = edit_dict[rid]
                            updated_rows.append([
                                er.get('ID', r.get('ID', '')),
                                er.get('작성일', r.get('작성일', '')),
                                er.get('공장', r.get('공장', '')),
                                er.get('안건내용', r.get('안건내용', '')),
                                er.get('담당자', r.get('담당자', '')),
                                er.get('상태', r.get('상태', '')),
                                er.get('비고', r.get('비고', ''))
                            ])
                        else:
                            updated_rows.append([r.get(h, "") for h in hd])
                    sheet_meetings.clear()
                    sheet_meetings.update([hd] + updated_rows)
                    st.success("✅ 저장됨"); st.cache_data.clear(); st.rerun()
            else: st.info("진행 중인 안건이 없습니다.")
        else: st.info("회의 데이터가 없습니다.")

    with tab_m2:
        with st.form("new_mtg"):
            n_date = st.date_input("날짜")
            n_fac  = st.selectbox("공장", ["1공장", "2공장", "공통"])
            n_con  = st.text_area("안건 내용")
            n_as   = st.text_input("담당자")
            if st.form_submit_button("📝 등록"):
                if not n_con.strip():
                    st.error("안건 내용을 입력해주세요.")
                else:
                    sheet_meetings.append_row([f"M-{int(time.time())}", n_date.strftime('%Y-%m-%d'), n_fac, n_con, n_as, "진행중", ""])
                    st.success("✅ 등록됨"); st.cache_data.clear(); st.rerun()

    with tab_m3:
        st.subheader("🔍 전체 안건 이력")
        if not df_meetings.empty:
            # 인쇄용 HTML 생성
            mtg_rows = ""
            for _, r in df_meetings.iterrows():
                status_color = "#d4edda" if r.get('상태') == '완료' else "#fff3cd"
                mtg_rows += f"<tr style='background:{status_color};'><td>{r.get('작성일','')}</td><td>{r.get('공장','')}</td><td>{r.get('안건내용','')}</td><td>{r.get('담당자','')}</td><td>{r.get('상태','')}</td><td>{r.get('비고','')}</td></tr>"
            html_mtg = f"<h2>현장 회의 안건 이력</h2><table border='1' style='width:100%;border-collapse:collapse;font-size:12px;'><thead><tr style='background:#ccc;'><th>작성일</th><th>공장</th><th>안건내용</th><th>담당자</th><th>상태</th><th>비고</th></tr></thead><tbody>{mtg_rows}</tbody></table>"
            st.dataframe(df_meetings, use_container_width=True, hide_index=True)
            st.components.v1.html(create_print_button(html_mtg, "회의이력", "landscape"), height=60)
        else: st.info("회의 데이터가 없습니다.")
