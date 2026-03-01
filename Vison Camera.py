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
import random

# ─── 페이지 설정 ───────────────────────────────────────────────
if os.path.exists("logo.png"):
    st.set_page_config(page_title="KPR ERP", page_icon="logo.png", layout="wide")
else:
    st.set_page_config(page_title="KPR ERP", page_icon="🏭", layout="wide")

# ─── 구글 시트 연결 ────────────────────────────────────────────
SPREADSHEET_ID = "1qLWcLwS-aTBPeCn39h0bobuZlpyepfY5Hqn-hsP-hvk"
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

@st.cache_resource
def get_doc():
    try:
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"]), scopes=SCOPES)
            return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    except Exception:
        pass
    if os.path.exists('key.json'):
        creds = Credentials.from_service_account_file('key.json', scopes=SCOPES)
        return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    return None

def get_or_create_sheet(doc, name, headers=None):
    if doc is None:
        return None
    try:
        return doc.worksheet(name)
    except Exception:
        if headers:
            try:
                ws = doc.add_worksheet(title=name, rows="1000", cols="20")
                ws.append_row(headers)
                return ws
            except Exception:
                return None
        return None

@st.cache_resource
def get_sheets():
    doc = get_doc()
    return {
        'items':      get_or_create_sheet(doc, 'Items'),
        'inventory':  get_or_create_sheet(doc, 'Inventory'),
        'logs':       get_or_create_sheet(doc, 'Logs'),
        'bom':        get_or_create_sheet(doc, 'BOM'),
        'orders':     get_or_create_sheet(doc, 'Orders',
                        ['주문번호','날짜','거래처','코드','품목명','수량','팔레트번호','상태','비고','거래처코드','타입']),
        'wastewater': get_or_create_sheet(doc, 'Wastewater',
                        ['날짜','대표자','환경기술인','가동시간','플라스틱재생칩','합성수지','안료','용수사용량','폐수발생량','위탁량','기타']),
        'meetings':   get_or_create_sheet(doc, 'Meetings',
                        ['ID','작성일','공장','안건내용','담당자','상태','비고']),
        'mapping':    get_or_create_sheet(doc, 'Print_Mapping', ['Code','Print_Name']),
    }

SH = get_sheets()

# ─── 데이터 로딩 ───────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_data():
    result = {}
    key_map = ['items','inventory','logs','bom','orders','wastewater','meetings','mapping']
    for key in key_map:
        ws = SH.get(key)
        df = pd.DataFrame()
        if ws:
            for _ in range(3):
                try:
                    rows = ws.get_all_records()
                    if rows:
                        df = pd.DataFrame(rows)
                        df = df.replace([np.inf, -np.inf], np.nan).fillna("")
                        if '수량' in df.columns:
                            df['수량'] = pd.to_numeric(df['수량'], errors='coerce').fillna(0.0)
                        if '현재고' in df.columns:
                            df['현재고'] = pd.to_numeric(df['현재고'], errors='coerce').fillna(0.0)
                    break
                except Exception:
                    time.sleep(0.5)
        result[key] = df
    return result

def sf(v):
    try: return float(v)
    except: return 0.0

def update_inv(factory, code, qty, name="-", spec="-", typ="-", color="-"):
    ws = SH.get('inventory')
    if not ws or code == '' or code == '-': return
    try:
        cells = ws.findall(str(code))
        target = next((c for c in cells if c.col == 2), None)
        if target:
            curr = sf(ws.cell(target.row, 7).value)
            ws.update_cell(target.row, 7, round(curr + qty, 4))
        elif qty > 0:
            ws.append_row([factory, code, name, spec, typ, color, qty])
    except Exception as e:
        st.warning(f"재고 업데이트 오류({code}): {e}")

def get_shape(code, df_items):
    if df_items.empty: return "-"
    r = df_items[df_items['코드'].astype(str) == str(code)]
    if r.empty: return "-"
    t = str(r.iloc[0].get('타입', '-'))
    if "원통" in t: return "CYLINDRIC"
    if "큐빅" in t: return "CUBICAL"
    if "펠렛" in t: return "PELLET"
    if "파우더" in t: return "POWDER"
    return t

def get_cat(row):
    name = str(row.get('품목명','')).upper()
    code = str(row.get('코드','')).upper()
    gubun = str(row.get('구분','')).strip()
    if 'CP' in name or 'COMPOUND' in name or 'CP' in code: return "Compound"
    if ('KA' in name or 'KA' in code) and (gubun=='반제품' or name.endswith('반') or '반' in name): return "KA반제품"
    if 'KA' in name or 'KA' in code: return "KA"
    if 'KG' in name or 'KG' in code: return "KG"
    if gubun=='반제품' or name.endswith('반'): return "반제품(기타)"
    return "기타"

def print_btn(html, title="Print", orient="portrait"):
    safe = html.replace('`','\\`').replace('$','\\$')
    fn = title.replace(" ","_").replace("/","_")
    css = f"@page{{size:A4 {orient};margin:1cm}}"
    return f"""<script>function prt_{fn}(){{
        var w=window.open('','','width=900,height=700');
        w.document.write('<html><head><style>{css} body{{font-family:sans-serif}} table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid black;padding:4px}}</style></head><body>');
        w.document.write(`{safe}`);w.document.write('</body></html>');
        w.document.close();w.focus();setTimeout(function(){{w.print()}},500);
    }}</script>
    <button onclick="prt_{fn}()" style="background:#4CAF50;border:none;color:white;padding:10px 20px;font-size:14px;cursor:pointer;border-radius:5px">🖨️ {title} 인쇄</button>"""

# ─── 로그인 ────────────────────────────────────────────────────
if "auth" not in st.session_state:
    st.session_state["auth"] = False

if not st.session_state["auth"]:
    st.title("🔒 KPR ERP 시스템")
    pw = st.text_input("접속 암호", type="password", key="pw")
    if st.button("로그인", type="primary"):
        correct = "kpr1234"
        try:
            correct = st.secrets.get("app_password", "kpr1234")
        except Exception:
            pass
        if pw == correct:
            st.session_state["auth"] = True
            st.rerun()
        else:
            st.error("암호가 틀렸습니다.")
    st.stop()

# ─── 데이터 로드 ───────────────────────────────────────────────
DATA = load_data()
df_items     = DATA['items']
df_inventory = DATA['inventory']
df_logs      = DATA['logs']
df_bom       = DATA['bom']
df_orders    = DATA['orders']
df_wastewater= DATA['wastewater']
df_meetings  = DATA['meetings']
df_mapping   = DATA['mapping']

if 'cart' not in st.session_state: st.session_state['cart'] = []

# ─── 사이드바 ──────────────────────────────────────────────────
with st.sidebar:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
    else:
        st.header("🏭 KPR / Chamstek")
    if st.button("🔄 새로고침"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()
    st.markdown("---")
    menu = st.radio("메뉴", [
        "대시보드",
        "재고/생산 관리",
        "영업/출고 관리",
        "현장 작업 (LOT 입력)",
        "이력/LOT 검색",
        "환경/폐수 일지",
        "주간 회의 & 개선사항"
    ])
    st.markdown("---")
    sel_date = st.date_input("날짜", datetime.datetime.now())
    time_str = datetime.datetime.now().strftime("%H:%M:%S")
    factory  = st.selectbox("공장", ["1공장", "2공장"])

# ══════════════════════════════════════════════════════════════
# [0] 대시보드
# ══════════════════════════════════════════════════════════════
if menu == "대시보드":
    st.title("📊 공장 현황 대시보드")
    if df_logs.empty:
        st.info("데이터를 불러오는 중입니다...")
    else:
        today = datetime.date.today()
        target = (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        label  = "어제"
        if '구분' in df_logs.columns:
            pdates = sorted(df_logs[df_logs['구분']=='생산']['날짜'].unique(), reverse=True)
            for d in pdates:
                try:
                    dd = pd.to_datetime(d).date()
                    if dd < today:
                        target = d
                        label  = "어제" if dd == today - datetime.timedelta(days=1) else "최근 작업일"
                        break
                except: continue

        df_day  = df_logs[df_logs['날짜'] == target]
        pdata   = df_day[df_day['구분']=='생산'].copy() if '구분' in df_day.columns else pd.DataFrame()
        tot=ka=kg=kaban=cp = 0
        if not pdata.empty:
            pdata['Cat'] = pdata.apply(get_cat, axis=1)
            tot   = pdata['수량'].sum()
            ka    = pdata[pdata['Cat']=='KA']['수량'].sum()
            kg    = pdata[pdata['Cat']=='KG']['수량'].sum()
            kaban = pdata[pdata['Cat']=='KA반제품']['수량'].sum()
            cp    = pdata[pdata['Cat']=='Compound']['수량'].sum()

        out_v = abs(df_day[df_day['구분']=='출고']['수량'].sum()) if '구분' in df_day.columns else 0
        pend  = len(df_orders[df_orders['상태']=='준비']['주문번호'].unique()) if not df_orders.empty and '상태' in df_orders.columns else 0

        st.subheader(f"📅 {label}({target}) 실적 요약")
        k1,k2,k3 = st.columns(3)
        k1.metric(f"{label} 총 생산", f"{tot:,.0f} kg")
        k1.markdown(f"<div style='font-size:13px;color:gray'>KA {ka:,.0f} / KG {kg:,.0f} / KA반제품 {kaban:,.0f} / CP {cp:,.0f}</div>", unsafe_allow_html=True)
        k2.metric(f"{label} 총 출고", f"{out_v:,.0f} kg")
        k3.metric("출고 대기", f"{pend} 건", delta="작업 필요", delta_color="inverse")

        # 이번달 누적
        this_m = today.strftime("%Y-%m")
        df_m = df_logs[(df_logs['구분']=='생산') & (df_logs['날짜'].astype(str).str.startswith(this_m))]
        m_tot = df_m['수량'].sum(); m_days = df_m['날짜'].nunique()
        st.markdown(f"""<div style='background:#f0f4ff;border-radius:8px;padding:10px 18px;margin:8px 0;display:flex;gap:40px'>
        <div><span style='color:#888;font-size:12px'>{this_m} 누적 생산</span><br><b style='font-size:20px;color:#1a5cad'>{m_tot:,.0f} kg</b></div>
        <div><span style='color:#888;font-size:12px'>작업일수</span><br><b style='font-size:20px;color:#1a5cad'>{m_days}일</b></div>
        <div><span style='color:#888;font-size:12px'>일평균</span><br><b style='font-size:20px;color:#1a5cad'>{(m_tot/m_days if m_days else 0):,.0f} kg</b></div>
        </div>""", unsafe_allow_html=True)
        st.markdown("---")

        st.subheader("📈 생산 추이")
        cf1, cf2 = st.columns([2,1])
        tdt = pd.to_datetime(target).date()
        sr  = cf1.date_input("조회 기간", [tdt-datetime.timedelta(days=6), tdt])
        fo  = cf2.selectbox("품목 필터", ["전체","KA","KG","KA반제품","Compound"])
        df_pl = df_logs[df_logs['구분']=='생산'].copy()
        if len(sr)==2:
            s_d,e_d = sr
            dates = pd.date_range(s_d, e_d)
            cats  = ["KA","KG","KA반제품","Compound","기타"]
            skel  = pd.DataFrame([{'날짜':d.strftime('%Y-%m-%d'),'Cat':c,'수량':0} for d in dates for c in cats])
            if not df_pl.empty:
                df_pl['날짜'] = pd.to_datetime(df_pl['날짜']).dt.strftime('%Y-%m-%d')
                df_pl['Cat'] = df_pl.apply(get_cat, axis=1)
                if fo!="전체": df_pl = df_pl[df_pl['Cat']==fo]
                real = df_pl.groupby(['날짜','Cat'])['수량'].sum().reset_index()
            else: real = pd.DataFrame(columns=['날짜','Cat','수량'])
            if fo!="전체": skel = skel[skel['Cat']==fo]
            fin = pd.merge(skel, real, on=['날짜','Cat'], how='left', suffixes=('_b','_r'))
            fin['수량'] = fin['수량_r'].fillna(0)
            fin['날짜_dt'] = pd.to_datetime(fin['날짜'])
            wmap = {0:'(월)',1:'(화)',2:'(수)',3:'(목)',4:'(금)',5:'(토)',6:'(일)'}
            fin['표시'] = fin['날짜_dt'].dt.strftime('%m-%d') + " " + fin['날짜_dt'].dt.dayofweek.map(wmap)
            ch = alt.Chart(fin).mark_bar().encode(
                x=alt.X('표시',title='날짜',axis=alt.Axis(labelAngle=0)),
                y=alt.Y('수량',title='생산량(kg)'),
                color=alt.Color('Cat',scale=alt.Scale(domain=cats,range=["#1f77b4","#ff7f0e","#17becf","#d62728","#9467bd"])),
                xOffset='Cat', tooltip=['표시','Cat',alt.Tooltip('수량',format=',.0f')]
            ).properties(height=320)
            st.altair_chart(ch, use_container_width=True)

# ══════════════════════════════════════════════════════════════
# [1] 재고/생산 관리
# ══════════════════════════════════════════════════════════════
elif menu == "재고/생산 관리":
    st.title(f"📦 재고/생산 관리 ({factory})")

    # 사이드바 입력 (sidebar with 사용하지 않고 expander로 대체)
    with st.sidebar:
        st.markdown("### 📝 작업 입력")
        cat = st.selectbox("구분", ["입고","생산","재고실사"])
        prod_line = "-"
        if cat == "생산":
            if factory == "1공장": lo = [f"압출{i}호" for i in range(1,6)] + ["기타"]
            else:                   lo = [f"압출{i}호" for i in range(1,7)] + [f"컷팅{i}호" for i in range(1,11)] + ["기타"]
            prod_line = st.selectbox("설비 라인", lo)

        sel_code = None; item_info = None; sys_q = 0.0

        if not df_items.empty:
            df_f = df_items.copy()
            for c in ['규격','타입','색상','품목명','구분']:
                if c in df_f.columns: df_f[c] = df_f[c].astype(str).str.strip()
            if cat=="입고":   df_f = df_f[df_f['구분']=='원자재']
            elif cat=="생산": df_f = df_f[df_f['구분'].isin(['제품','완제품','반제품'])]

            def get_group(row):
                nm = str(row.get('품목명','')).upper(); g = str(row.get('구분',''))
                if g=='반제품' or nm.endswith('반'): return "반제품"
                if "CP" in nm or "COMPOUND" in nm: return "COMPOUND"
                if "KG" in nm: return "KG"
                if "KA" in nm: return "KA"
                return "기타"
            df_f['Group'] = df_f.apply(get_group, axis=1)

            grps = sorted(df_f['Group'].unique())
            grp  = st.selectbox("1.그룹", grps)
            df1  = df_f[df_f['Group']==grp]
            final = pd.DataFrame()

            if grp == "반제품":
                pn = st.selectbox("2.품목명", sorted(df1['품목명'].unique()))
                final = df1[df1['품목명']==pn]
            elif grp == "COMPOUND":
                cl = st.selectbox("2.색상", sorted(df1['색상'].unique()))
                final = df1[df1['색상']==cl]
            elif cat == "입고":
                sl = sorted(df1['규격'].unique())
                sp = st.selectbox("2.규격", sl) if sl else None
                final = df1[df1['규격']==sp] if sp else df1
            else:
                sp  = st.selectbox("2.규격", sorted(df1['규격'].unique()))
                df2 = df1[df1['규격']==sp]
                if not df2.empty:
                    cl  = st.selectbox("3.색상", sorted(df2['색상'].unique()))
                    df3 = df2[df2['색상']==cl]
                    if not df3.empty:
                        tp  = st.selectbox("4.타입", sorted(df3['타입'].unique()))
                        final = df3[df3['타입']==tp]

            if not final.empty:
                item_info = final.iloc[0]; sel_code = str(item_info['코드'])
                st.success(f"선택: {sel_code}")
                if cat=="재고실사" and not df_inventory.empty:
                    inv_r = df_inventory[df_inventory['코드'].astype(str)==sel_code]
                    sys_q = inv_r['현재고'].apply(sf).sum()
                    st.info(f"전산 재고: {sys_q:,.1f}")

        qty_in  = st.number_input("수량") if cat!="재고실사" else 0.0
        note_in = st.text_input("비고")
        if cat=="재고실사":
            real   = st.number_input("실사값", value=float(sys_q))
            qty_in = real - sys_q
            note_in = f"[실사] {note_in}"

        if st.button("저장"):
            if item_info is None:
                st.error("품목을 선택하세요.")
            elif qty_in==0 and cat!="재고실사":
                st.warning("수량이 0입니다.")
            else:
                ws_logs = SH.get('logs')
                if ws_logs:
                    try:
                        ws_logs.append_row([
                            sel_date.strftime('%Y-%m-%d'), time_str, factory, cat,
                            sel_code, item_info.get('품목명',''), item_info.get('규격',''),
                            item_info.get('타입',''), item_info.get('색상',''),
                            qty_in, note_in, "-", prod_line
                        ])
                        update_inv(factory, sel_code, qty_in,
                                   item_info.get('품목명',''), item_info.get('규격',''),
                                   item_info.get('타입',''), item_info.get('색상',''))
                        if cat=="생산" and not df_bom.empty:
                            bt = df_bom[df_bom['제품코드'].astype(str)==sel_code]
                            if '타입' in df_bom.columns:
                                bt = bt[bt['타입'].astype(str)==str(item_info.get('타입',''))]
                            bt = bt.drop_duplicates(subset=['자재코드'])
                            for _,r in bt.iterrows():
                                req = qty_in * sf(r['소요량'])
                                update_inv(factory, str(r['자재코드']), -req)
                                time.sleep(0.3)
                                ws_logs.append_row([
                                    sel_date.strftime('%Y-%m-%d'), time_str, factory, "사용(Auto)",
                                    r['자재코드'], "System", "-", "-", "-", -req,
                                    f"{sel_code} 생산", "-", prod_line
                                ])
                        st.cache_data.clear(); st.success("✅ 저장 완료"); st.rerun()
                    except Exception as e:
                        st.error(f"저장 오류: {e}")

    t1,t2,t3,t4,t5 = st.tabs(["🏭 생산이력","📥 입고이력","📦 재고현황","📜 전체로그","🔩 BOM"])

    with t1:
        st.subheader("생산 이력")
        if not df_logs.empty and '구분' in df_logs.columns:
            df_p = df_logs[df_logs['구분']=='생산'].copy()
            df_p['No'] = df_p.index + 2
            cols_p = list(df_p.columns)
            if len(cols_p) > 12: cols_p[12] = '라인'
            df_p.columns = cols_p
            if '라인' not in df_p.columns: df_p['라인'] = '-'

            fc1,fc2,fc3,fc4 = st.columns(4)
            min_d = pd.to_datetime(df_p['날짜']).min().date() if not df_p.empty else datetime.date.today()
            sd = fc1.date_input("날짜범위", [min_d, datetime.date.today()], key="p_date")
            all_lines = ["전체"] + sorted(df_p['라인'].astype(str).unique().tolist())
            sl2 = fc2.selectbox("라인", all_lines)
            sk  = fc3.text_input("코드/품목명", key="p_txt")
            sf2 = fc4.selectbox("공장", ["전체","1공장","2공장"])

            df_r = df_p.copy()
            if len(sd)==2:
                df_r['날짜'] = pd.to_datetime(df_r['날짜'])
                df_r = df_r[(df_r['날짜'].dt.date>=sd[0]) & (df_r['날짜'].dt.date<=sd[1])]
                df_r['날짜'] = df_r['날짜'].dt.strftime('%Y-%m-%d')
            if sl2!="전체": df_r = df_r[df_r['라인']==sl2]
            if sk: df_r = df_r[df_r['코드'].astype(str).str.contains(sk,case=False)|df_r['품목명'].astype(str).str.contains(sk,case=False)]
            if sf2!="전체": df_r = df_r[df_r['공장']==sf2]

            dc = [c for c in ['No','날짜','시간','공장','라인','코드','품목명','타입','수량','비고'] if c in df_r.columns]
            st.dataframe(df_r[dc].sort_values(['날짜','시간'],ascending=False), use_container_width=True, hide_index=True)

            st.markdown("### 기록 관리")
            opts = {r['No']: f"No.{r['No']} | {r['날짜']} {r['품목명']} ({r['수량']}kg)" for _,r in df_r.iterrows()}
            if opts:
                sel_id = st.selectbox("기록 선택", list(opts.keys()), format_func=lambda x: opts[x])
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("🗑️ 삭제", type="primary"):
                        tr = df_p[df_p['No']==sel_id].iloc[0]
                        update_inv(tr['공장'], str(tr['코드']), -sf(tr['수량']))
                        ws_logs = SH.get('logs')
                        if ws_logs:
                            try:
                                live = ws_logs.get_all_records()
                                to_del = [sel_id]
                                for i,r in enumerate(live):
                                    if (str(r.get('날짜',''))==str(tr['날짜']) and
                                        str(r.get('시간',''))==str(tr['시간']) and
                                        r.get('구분','')=='사용(Auto)' and
                                        str(tr['코드']) in str(r.get('비고',''))):
                                        update_inv(tr['공장'], str(r.get('코드','')), -sf(r.get('수량',0)))
                                        to_del.append(i+2)
                                for idx in sorted(set(to_del), reverse=True):
                                    ws_logs.delete_rows(int(idx)); time.sleep(0.3)
                                st.success("삭제 완료"); st.cache_data.clear(); st.rerun()
                            except Exception as e: st.error(f"오류: {e}")
        else:
            st.info("생산 데이터가 없습니다.")

    with t2:
        st.subheader("원자재 입고 이력")
        if not df_logs.empty and '구분' in df_logs.columns:
            df_in = df_logs[df_logs['구분']=='입고'].copy()
            df_in['No'] = df_in.index + 2
            ic1,ic2 = st.columns(2)
            min_di = pd.to_datetime(df_in['날짜']).min().date() if not df_in.empty else datetime.date.today()
            isd = ic1.date_input("날짜범위", [min_di, datetime.date.today()], key="r_date")
            isk = ic2.text_input("품목 검색", key="r_txt")
            df_ri = df_in.copy()
            if len(isd)==2:
                df_ri['날짜'] = pd.to_datetime(df_ri['날짜'])
                df_ri = df_ri[(df_ri['날짜'].dt.date>=isd[0]) & (df_ri['날짜'].dt.date<=isd[1])]
                df_ri['날짜'] = df_ri['날짜'].dt.strftime('%Y-%m-%d')
            if isk: df_ri = df_ri[df_ri['품목명'].astype(str).str.contains(isk,case=False)]
            dc2 = [c for c in ['No','날짜','시간','공장','코드','품목명','규격','수량','비고'] if c in df_ri.columns]
            st.dataframe(df_ri[dc2].sort_values(['날짜','시간'],ascending=False), use_container_width=True, hide_index=True)
            opts2 = {r['No']: f"No.{r['No']} | {r['날짜']} {r['품목명']} ({r['수량']}kg)" for _,r in df_ri.iterrows()}
            if opts2:
                sid2 = st.selectbox("삭제할 기록", list(opts2.keys()), format_func=lambda x: opts2[x])
                if st.button("❌ 입고 삭제", type="primary"):
                    tr2 = df_in[df_in['No']==sid2].iloc[0]
                    update_inv(tr2['공장'], str(tr2['코드']), -sf(tr2['수량']))
                    ws_logs = SH.get('logs')
                    if ws_logs:
                        ws_logs.delete_rows(int(sid2))
                    st.success("삭제 완료"); st.cache_data.clear(); st.rerun()

    with t3:
        st.subheader("재고 현황")
        if not df_inventory.empty:
            df_v = df_inventory.copy()
            if not df_items.empty and '코드' in df_items.columns and '구분' in df_items.columns:
                cmap = df_items.drop_duplicates('코드').set_index('코드')['구분'].to_dict()
                df_v['구분'] = df_v['코드'].map(cmap).fillna('-')
            vc1,vc2,vc3 = st.columns(3)
            ff = vc1.radio("공장", ["전체","1공장","2공장"], horizontal=True)
            cf = vc2.radio("품목", ["전체","제품","반제품","원자재"], horizontal=True)
            ls = vc3.checkbox("⚠️ 저재고만")
            sk3 = st.text_input("검색", key="inv_s")
            if ff!="전체": df_v = df_v[df_v['공장']==ff]
            if cf!="전체":
                if cf=="제품": df_v = df_v[df_v.get('구분', pd.Series()).isin(['제품','완제품'])] if '구분' in df_v.columns else df_v
                elif '구분' in df_v.columns: df_v = df_v[df_v['구분']==cf]
            if sk3 and '코드' in df_v.columns:
                df_v = df_v[df_v['코드'].astype(str).str.contains(sk3,case=False)|df_v['품목명'].astype(str).str.contains(sk3,case=False)]
            if ls and '현재고' in df_v.columns:
                df_v = df_v[pd.to_numeric(df_v['현재고'],errors='coerce').fillna(0)<=0]
            st.dataframe(df_v, use_container_width=True)
        else: st.info("재고 데이터 없음")

    with t4: st.dataframe(df_logs, use_container_width=True)
    with t5: st.dataframe(df_bom, use_container_width=True)

# ══════════════════════════════════════════════════════════════
# [2] 영업/출고 관리
# ══════════════════════════════════════════════════════════════
elif menu == "영업/출고 관리":
    st.title("📑 영업 주문 및 출고 관리")
    ws_ord = SH.get('orders')
    if ws_ord is None:
        st.error("'Orders' 시트 연결 실패. 새로고침을 눌러주세요.")
    else:
        tab_o,tab_p,tab_prt,tab_out,tab_cancel = st.tabs([
            "📝 주문등록","✏️ 팔레트수정","🖨️ 인쇄","🚚 출고확정","↩️ 출고취소"
        ])

        with tab_o:
            c1,c2 = st.columns([1,2])
            with c1:
                st.subheader("주문 입력")
                od_dt = st.date_input("주문일", datetime.datetime.now())
                cl_nm = st.text_input("거래처명", placeholder="예: SHANGHAI YILIU")
                if not df_items.empty:
                    df_sale = df_items[df_items['구분'].isin(['제품','완제품'])].copy()
                    df_sale['Disp'] = df_sale['코드'].astype(str)+" ("+df_sale['규격'].astype(str)+"/"+df_sale['색상'].astype(str)+"/"+df_sale['타입'].astype(str)+")"
                    sel_it = st.selectbox("품목", df_sale['Disp'].unique())
                    row_it = df_sale[df_sale['Disp']==sel_it].iloc[0]
                    ord_q  = st.number_input("주문량(kg)", step=100.0)
                    ord_r  = st.text_input("포장단위", value="BOX")
                    if st.button("🛒 담기"):
                        st.session_state['cart'].append({
                            "코드":row_it['코드'],"품목명":row_it['품목명'],"규격":row_it['규격'],
                            "색상":row_it['색상'],"타입":row_it['타입'],"수량":ord_q,"비고":ord_r
                        }); st.rerun()
            with c2:
                st.subheader("🛒 장바구니")
                if st.session_state['cart']:
                    for i,it in enumerate(st.session_state['cart']):
                        a,b,c3 = st.columns([4,2,1])
                        a.write(f"**{it['코드']}** {it['품목명']}")
                        b.write(f"{it['수량']:,}kg/{it['비고']}")
                        if c3.button("❌",key=f"cd{i}"): st.session_state['cart'].pop(i); st.rerun()
                    st.markdown("---")
                    mx = st.number_input("팔레트당 최대(kg)", min_value=100.0, value=1000.0, step=100.0)
                    ba,bb = st.columns(2)
                    if ba.button("비우기"): st.session_state['cart']=[]; st.rerun()
                    if bb.button("✅ 주문확정", type="primary"):
                        oid="ORD-"+datetime.datetime.now().strftime("%y%m%d%H%M")
                        rows=[]; pn=1; cw=0
                        for it in st.session_state['cart']:
                            rem=it['수량']
                            while rem>0:
                                sp=mx-cw
                                if sp<=0: pn+=1;cw=0;sp=mx
                                ld=min(rem,sp)
                                rows.append([oid,od_dt.strftime('%Y-%m-%d'),cl_nm,it['코드'],it['품목명'],ld,pn,"준비",it['비고'],"",it['타입']])
                                cw+=ld;rem-=ld
                        for r in rows: ws_ord.append_row(r)
                        st.session_state['cart']=[]; st.cache_data.clear(); st.success("주문 저장!"); st.rerun()

        with tab_p:
            st.subheader("팔레트 수정/재구성")
            if not df_orders.empty and '상태' in df_orders.columns:
                pend = df_orders[df_orders['상태']=='준비']
                if not pend.empty:
                    uord = pend[['주문번호','날짜','거래처']].drop_duplicates().set_index('주문번호').to_dict('index')
                    tgt  = st.selectbox("주문선택", pend['주문번호'].unique(), format_func=lambda x:f"{uord[x]['날짜']}|{uord[x]['거래처']}({x})")
                    odf  = pend[pend['주문번호']==tgt].copy()
                    odf['Real_Index'] = range(len(odf))
                    odf['팔레트번호'] = pd.to_numeric(odf['팔레트번호'],errors='coerce').fillna(999)
                    ddf  = odf.sort_values('팔레트번호')
                    dc3  = [c for c in ['팔레트번호','코드','품목명','수량','비고'] if c in ddf.columns]
                    st.dataframe(ddf[dc3], use_container_width=True, hide_index=True)

                    with st.expander("📦 팔레트 재구성"):
                        nmx = st.number_input("새 팔레트당(kg)", min_value=100.0, value=1200.0, step=100.0)
                        if st.button("🚀 재구성"):
                            comb = odf.groupby(['코드','품목명','비고','타입'])['수량'].sum().reset_index()
                            nr=[]; pc=1; cw2=0
                            for _,r in comb.iterrows():
                                rem=r['수량']
                                while rem>0:
                                    sp=nmx-cw2
                                    if sp<=0: pc+=1;cw2=0;sp=nmx
                                    ld=min(rem,sp)
                                    nr.append([tgt,odf.iloc[0]['날짜'],odf.iloc[0]['거래처'],r['코드'],r['품목명'],ld,pc,"준비",r['비고'],"",r['타입']])
                                    cw2+=ld;rem-=ld
                            all_r=ws_ord.get_all_records(); hd=ws_ord.row_values(1)
                            fr=[r for r in all_r if str(r['주문번호'])!=str(tgt)]
                            ws_ord.clear(); ws_ord.update([hd]+[[r.get(h,"") for h in hd] for r in fr]+nr)
                            st.success("재구성 완료"); st.cache_data.clear(); st.rerun()

        with tab_prt:
            st.subheader("🖨️ 명세서/라벨 인쇄")
            if not df_orders.empty and '상태' in df_orders.columns:
                pend2 = df_orders[df_orders['상태']=='준비']
                if not pend2.empty:
                    uord2 = pend2[['주문번호','날짜','거래처']].drop_duplicates().set_index('주문번호').to_dict('index')
                    tgt2  = st.selectbox("주문", pend2['주문번호'].unique(), key='prt_sel',
                                         format_func=lambda x:f"{uord2[x]['날짜']}|{uord2[x]['거래처']}({x})")
                    dp = pend2[pend2['주문번호']==tgt2].copy()
                    dp['팔레트번호'] = pd.to_numeric(dp['팔레트번호'],errors='coerce').fillna(999)
                    dp = dp.sort_values('팔레트번호')
                    if not dp.empty:
                        cli = dp.iloc[0]['거래처']
                        saved_map = dict(zip(df_mapping['Code'].astype(str), df_mapping['Print_Name'].astype(str))) if not df_mapping.empty else {}
                        cm_data = [{"Internal":str(c),"Print_Name":saved_map.get(str(c),str(c))} for c in sorted(dp['코드'].unique())]
                        edited_map = st.data_editor(pd.DataFrame(cm_data), use_container_width=True, hide_index=True)
                        code_map = dict(zip(edited_map['Internal'], edited_map['Print_Name']))

                        if st.button("💾 이름 저장"):
                            ws_mp = SH.get('mapping')
                            if ws_mp:
                                db_m = dict(saved_map); db_m.update(code_map)
                                ws_mp.clear(); ws_mp.update([["Code","Print_Name"]]+[[k,v] for k,v in db_m.items()])
                                st.success("저장됨"); st.cache_data.clear(); st.rerun()

                        st.subheader("📄 Packing List")
                        pl_rows=""; tot_q=0
                        for pn3, grp3 in dp.groupby('팔레트번호'):
                            gl=len(grp3); first=True
                            for _,r in grp3.iterrows():
                                shp=get_shape(r['코드'],df_items)
                                dn=code_map.get(str(r['코드']),str(r['코드']))
                                pl_rows+=f"<tr>"
                                if first: pl_rows+=f"<td rowspan='{gl}'>{pn3}</td>"
                                pl_rows+=f"<td>{dn}</td><td align='right'>{r['수량']:,.0f}</td><td>{shp}</td><td>{r['비고']}</td></tr>"
                                first=False; tot_q+=r['수량']
                        html_pl=f"<h2>PACKING LIST - {cli}</h2><p>Total: {tot_q:,.0f} kg</p><table border='1'><tr style='background:#eee'><th>PLT</th><th>ITEM</th><th>QTY</th><th>SHAPE</th><th>REMARK</th></tr>{pl_rows}</table>"
                        st.components.v1.html(html_pl, height=400, scrolling=True)
                        st.components.v1.html(print_btn(html_pl,"PackingList","landscape"), height=55)

        with tab_out:
            st.subheader("🚚 출고 확정")
            if not df_orders.empty and '상태' in df_orders.columns:
                pend3 = df_orders[df_orders['상태']=='준비']
                if not pend3.empty:
                    uord3 = pend3[['주문번호','날짜','거래처']].drop_duplicates().set_index('주문번호').to_dict('index')
                    tgt3  = st.selectbox("출고 주문", pend3['주문번호'].unique(), format_func=lambda x:f"{uord3[x]['날짜']}|{uord3[x]['거래처']}({x})")
                    do    = pend3[pend3['주문번호']==tgt3]
                    dc4   = [c for c in ['코드','품목명','수량','팔레트번호'] if c in do.columns]
                    st.dataframe(do[dc4], use_container_width=True)
                    if st.button("🚀 출고 확정", type="primary"):
                        ws_logs = SH.get('logs')
                        for _,row in do.iterrows():
                            qo=sf(row['수량'])
                            update_inv(factory,str(row['코드']),-qo)
                            if ws_logs:
                                ws_logs.append_row([datetime.date.today().strftime('%Y-%m-%d'),time_str,factory,"출고",
                                    row['코드'],row['품목명'],"-",row.get('타입','-'),"-",-qo,f"주문출고({tgt3})",row['거래처'],"-"])
                            time.sleep(0.2)
                        all_r=ws_ord.get_all_records(); hd=ws_ord.row_values(1)
                        upd=[hd]+[[(r.get(h,"") if h!='상태' else ('완료' if r['주문번호']==tgt3 else r.get('상태',''))) for h in hd] for r in all_r]
                        ws_ord.clear(); ws_ord.update(upd)
                        st.success("출고 완료"); st.cache_data.clear(); st.rerun()
                else: st.info("준비 중인 주문 없음")

        with tab_cancel:
            st.subheader("↩️ 출고 취소")
            if not df_orders.empty and '상태' in df_orders.columns:
                done = df_orders[df_orders['상태']=='완료']
                if not done.empty:
                    udone = done[['주문번호','날짜','거래처']].drop_duplicates().set_index('주문번호').to_dict('index')
                    tgt4  = st.selectbox("취소 주문", done['주문번호'].unique(), format_func=lambda x:f"{udone[x]['날짜']}|{udone[x]['거래처']}({x})")
                    dc_   = done[done['주문번호']==tgt4]
                    st.dataframe(dc_[['코드','품목명','수량']], use_container_width=True)
                    st.info(f"복구 예정: {dc_['수량'].sum():,.0f} kg")
                    if st.button("↩️ 취소 실행", type="primary"):
                        for _,row in dc_.iterrows():
                            update_inv(factory,str(row['코드']),sf(row['수량'])); time.sleep(0.2)
                        ws_logs = SH.get('logs')
                        if ws_logs:
                            try:
                                live=ws_logs.get_all_records(); di=[]
                                for i,r in enumerate(live):
                                    if r.get('구분','')=='출고' and str(tgt4) in str(r.get('비고','')):
                                        di.append(i+2)
                                for idx in sorted(di,reverse=True):
                                    ws_logs.delete_rows(idx); time.sleep(0.3)
                            except Exception as e: st.warning(f"로그 삭제 오류: {e}")
                        all_r=ws_ord.get_all_records(); hd=ws_ord.row_values(1)
                        upd=[hd]+[[(r.get(h,"") if h!='상태' else ('준비' if r['주문번호']==tgt4 else r.get('상태',''))) for h in hd] for r in all_r]
                        ws_ord.clear(); ws_ord.update(upd)
                        st.success("취소 완료"); st.cache_data.clear(); st.rerun()
                else: st.info("완료된 출고 없음")

# ══════════════════════════════════════════════════════════════
# [3] 현장 작업 (LOT 입력)
# ══════════════════════════════════════════════════════════════
elif menu == "현장 작업 (LOT 입력)":
    st.title("🏭 현장 작업 입력")
    st.caption("현장 작업자용 간편 입력 화면입니다.")

    c1,c2,c3 = st.columns(3)
    lot_date = c1.date_input("작업일", datetime.date.today(), key="ld")
    lot_fac  = c2.selectbox("공장", ["1공장","2공장"], key="lf")
    lot_cat  = c3.selectbox("구분", ["생산","입고"], key="lc")

    c4,c5 = st.columns(2)
    if lot_cat == "생산":
        if lot_fac=="1공장": lopts=[f"압출{i}호" for i in range(1,6)]+["기타"]
        else:                 lopts=[f"압출{i}호" for i in range(1,7)]+[f"컷팅{i}호" for i in range(1,11)]+["기타"]
        lot_line = c4.selectbox("설비 라인", lopts, key="ll")
    else:
        lot_line = "-"

    lot_row = None
    if df_items.empty:
        st.warning("품목 데이터가 없습니다. 새로고침을 눌러주세요.")
    else:
        df_li = df_items.copy()
        if '구분' in df_li.columns:
            if lot_cat=="생산": df_li = df_li[df_li['구분'].isin(['제품','완제품','반제품'])]
            else:               df_li = df_li[df_li['구분']=='원자재']
        if df_li.empty: df_li = df_items.copy()

        for col in ['코드','품목명','규격']:
            if col not in df_li.columns: df_li[col]=''
        df_li['Disp'] = df_li['코드'].astype(str)+" | "+df_li['품목명'].astype(str)+" ("+df_li['규격'].astype(str)+")"
        lot_sel = c5.selectbox("품목 선택", df_li['Disp'].unique(), key="li")
        m = df_li[df_li['Disp']==lot_sel]
        if not m.empty: lot_row = m.iloc[0]

    c6,c7 = st.columns(2)
    lot_qty  = c6.number_input("수량 (kg)", min_value=0.0, step=10.0, key="lq")
    lot_note = c7.text_input("비고 (LOT번호 등)", key="ln")

    if lot_row is not None:
        st.success(f"선택: **{lot_row.get('코드','')}** | {lot_row.get('품목명','')} | {lot_row.get('규격','')} | {lot_row.get('타입','')} | {lot_row.get('색상','')}")

    if st.button("✅ 저장", type="primary", key="lsave"):
        if lot_row is None:
            st.error("품목을 선택하세요.")
        elif lot_qty <= 0:
            st.error("수량을 입력하세요.")
        else:
            ws_logs = SH.get('logs')
            if not ws_logs:
                st.error("시트 연결 오류. 새로고침 후 재시도.")
            else:
                try:
                    now = datetime.datetime.now().strftime("%H:%M:%S")
                    ws_logs.append_row([
                        lot_date.strftime('%Y-%m-%d'), now, lot_fac, lot_cat,
                        lot_row.get('코드',''), lot_row.get('품목명',''), lot_row.get('규격','-'),
                        lot_row.get('타입','-'), lot_row.get('색상','-'),
                        lot_qty, lot_note, "-", lot_line
                    ])
                    update_inv(lot_fac, lot_row.get('코드',''), lot_qty,
                               lot_row.get('품목명',''), lot_row.get('규격','-'),
                               lot_row.get('타입','-'), lot_row.get('색상','-'))
                    if lot_cat=="생산" and not df_bom.empty:
                        bt=df_bom[df_bom['제품코드'].astype(str)==str(lot_row.get('코드',''))]
                        if '타입' in df_bom.columns:
                            bt=bt[bt['타입'].astype(str)==str(lot_row.get('타입',''))]
                        bt=bt.drop_duplicates(subset=['자재코드'])
                        for _,r in bt.iterrows():
                            req=lot_qty*sf(r['소요량'])
                            update_inv(lot_fac,str(r['자재코드']),-req)
                            time.sleep(0.3)
                            ws_logs.append_row([lot_date.strftime('%Y-%m-%d'),now,lot_fac,"사용(Auto)",
                                r['자재코드'],"System","-","-","-",-req,f"{lot_row.get('코드','')} 생산","-",lot_line])
                    st.cache_data.clear()
                    st.success(f"✅ {lot_cat} {lot_qty:,.0f}kg 저장 완료!")
                    st.rerun()
                except Exception as e:
                    st.error(f"저장 오류: {e}")

    st.markdown("---")
    st.subheader(f"📋 오늘 작업 현황 ({datetime.date.today()})")
    if not df_logs.empty and '구분' in df_logs.columns:
        today_s = datetime.date.today().strftime('%Y-%m-%d')
        df_tod  = df_logs[(df_logs['날짜'].astype(str).str[:10]==today_s) & (df_logs['구분'].isin(['생산','입고']))]
        if not df_tod.empty:
            dc5=[c for c in ['시간','공장','구분','코드','품목명','수량','비고'] if c in df_tod.columns]
            st.dataframe(df_tod[dc5].sort_values('시간',ascending=False), use_container_width=True, hide_index=True)
            st.metric("오늘 총 생산량", f"{df_tod[df_tod['구분']=='생산']['수량'].sum():,.0f} kg")
        else:
            st.info("오늘 작업 기록이 없습니다.")
    else:
        st.info("데이터가 없습니다.")

# ══════════════════════════════════════════════════════════════
# [4] 이력/LOT 검색
# ══════════════════════════════════════════════════════════════
elif menu == "이력/LOT 검색":
    st.title("🔍 이력 및 LOT 통합 검색")

    s1,s2,s3 = st.columns(3)
    kw  = s1.text_input("키워드 (코드/품목명/비고)", placeholder="예: KA100", key="sk")
    stp = s2.multiselect("구분", ["생산","입고","출고","사용(Auto)","재고실사"],
                          default=["생산","입고","출고"], key="stp")
    sfac= s3.radio("공장", ["전체","1공장","2공장"], horizontal=True, key="sfac")

    d1,d2 = st.columns(2)
    ss = d1.date_input("시작일", datetime.date.today()-datetime.timedelta(days=30), key="ss")
    se = d2.date_input("종료일", datetime.date.today(), key="se")

    st.markdown("---")

    if df_logs.empty:
        st.warning("로그 데이터가 없습니다. 새로고침을 눌러주세요.")
    else:
        df_s = df_logs.copy()
        if '날짜' in df_s.columns:
            df_s['날짜_dt'] = pd.to_datetime(df_s['날짜'], errors='coerce')
            df_s = df_s[df_s['날짜_dt'].notna()]
            df_s = df_s[(df_s['날짜_dt'].dt.date>=ss)&(df_s['날짜_dt'].dt.date<=se)]
            df_s['날짜'] = df_s['날짜_dt'].dt.strftime('%Y-%m-%d')
            df_s = df_s.drop(columns=['날짜_dt'])
        if stp and '구분' in df_s.columns:
            df_s = df_s[df_s['구분'].isin(stp)]
        if sfac!="전체" and '공장' in df_s.columns:
            df_s = df_s[df_s['공장']==sfac]
        if kw.strip():
            mask = pd.Series(False, index=df_s.index)
            for col in ['코드','품목명','비고']:
                if col in df_s.columns:
                    mask = mask | df_s[col].astype(str).str.contains(kw.strip(), case=False, na=False)
            df_s = df_s[mask]

        st.write(f"검색 결과: **{len(df_s)}건**")
        if not df_s.empty:
            sc = [c for c in ['날짜','시간','공장','구분','코드','품목명','규격','타입','색상','수량','비고'] if c in df_s.columns]
            srt= [c for c in ['날짜','시간'] if c in df_s.columns]
            st.dataframe(df_s[sc].sort_values(srt,ascending=False) if srt else df_s[sc],
                         use_container_width=True, hide_index=True)

            st.markdown("---")
            m1,m2,m3 = st.columns(3)
            if '구분' in df_s.columns and '수량' in df_s.columns:
                m1.metric("총 생산량", f"{df_s[df_s['구분']=='생산']['수량'].sum():,.0f} kg")
                m2.metric("총 출고량", f"{abs(df_s[df_s['구분']=='출고']['수량'].sum()):,.0f} kg")
                m3.metric("총 입고량", f"{df_s[df_s['구분']=='입고']['수량'].sum():,.0f} kg")

            gc = [c for c in ['코드','품목명','구분'] if c in df_s.columns]
            if gc and '수량' in df_s.columns:
                ag = df_s.groupby(gc)['수량'].sum().reset_index()
                ag['수량'] = ag['수량'].round(2)
                st.markdown("##### 품목별 집계")
                st.dataframe(ag.sort_values('수량',ascending=False), use_container_width=True, hide_index=True)
        else:
            st.info("검색 결과가 없습니다.")

# ══════════════════════════════════════════════════════════════
# [5] 환경/폐수 일지
# ══════════════════════════════════════════════════════════════
elif menu == "환경/폐수 일지":
    st.title("🌊 폐수배출시설 운영일지")
    tw1,tw2 = st.tabs(["📅 일지 작성","📋 이력 조회"])
    ws_ww = SH.get('wastewater')

    with tw1:
        st.markdown("### 월간 운영일지 작성")
        wc1,wc2,wc3 = st.columns(3)
        yr  = wc1.number_input("연도", 2024, 2030, datetime.date.today().year)
        mo  = wc2.number_input("월", 1, 12, datetime.date.today().month)
        rnd = wc3.checkbox("랜덤 변주(±1%)")
        if st.button("📝 일지 생성"):
            sd2 = datetime.date(yr,mo,1)
            ed2 = datetime.date(yr+1,1,1)-datetime.timedelta(1) if mo==12 else datetime.date(yr,mo+1,1)-datetime.timedelta(1)
            rows=[]
            for d in pd.date_range(sd2,ed2):
                dd=d.date(); ds=d.strftime('%Y-%m-%d')
                wk=["월","화","수","목","금","토","일"][dd.weekday()]
                fd=f"{d.strftime('%Y년 %m월 %d일')} {wk}요일"
                dp2=df_logs[(df_logs['날짜']==ds)&(df_logs['공장']=='1공장')&(df_logs['구분']=='생산')] if not df_logs.empty else pd.DataFrame()
                if not dp2.empty:
                    tq=dp2['수량'].sum(); rs=round(tq*0.8)
                    tm="08:00~15:00" if dd.weekday()==5 else "08:00~08:00"
                    if rnd: rs=round(rs*random.uniform(0.99,1.01))
                    rows.append({"날짜":fd,"대표자":"문성인","환경기술인":"문주혁","가동시간":tm,
                                 "플라스틱재생칩":0,"합성수지":rs,"안료":0.2,"용수사용량":2.16,"폐수발생량":0,"위탁량":"","기타":"전량 재이용"})
                else:
                    rows.append({"날짜":fd,"대표자":"","환경기술인":"","가동시간":"",
                                 "플라스틱재생칩":"","합성수지":"","안료":"","용수사용량":"","폐수발생량":"","위탁량":"","기타":""})
            st.session_state['ww_preview']=pd.DataFrame(rows); st.rerun()
        if 'ww_preview' in st.session_state:
            edited_ww=st.data_editor(st.session_state['ww_preview'],num_rows="dynamic",use_container_width=True)
            if st.button("💾 저장"):
                if ws_ww:
                    for _,r in edited_ww.iterrows(): ws_ww.append_row(list(r.values))
                del st.session_state['ww_preview']
                st.success("저장됨"); st.cache_data.clear(); st.rerun()

    with tw2:
        st.markdown("### 이력 조회")
        if not df_wastewater.empty:
            wk2=st.text_input("키워드 검색", key="wk2")
            df_wv=df_wastewater.copy()
            if wk2: df_wv=df_wv[df_wv.apply(lambda r:r.astype(str).str.contains(wk2,case=False).any(),axis=1)]
            st.dataframe(df_wv, use_container_width=True, hide_index=True)
            if '합성수지' in df_wv.columns:
                df_wc=df_wv.copy(); df_wc['합성수지_n']=pd.to_numeric(df_wc['합성수지'],errors='coerce')
                df_wc=df_wc.dropna(subset=['합성수지_n'])
                if not df_wc.empty:
                    wch=alt.Chart(df_wc).mark_line(point=True).encode(
                        x=alt.X('날짜:N',title='날짜'), y=alt.Y('합성수지_n:Q',title='합성수지'),
                        tooltip=['날짜','합성수지_n']
                    ).properties(height=250)
                    st.altair_chart(wch, use_container_width=True)
        else: st.info("저장된 일지가 없습니다.")

# ══════════════════════════════════════════════════════════════
# [6] 주간 회의 & 개선사항
# ══════════════════════════════════════════════════════════════
elif menu == "주간 회의 & 개선사항":
    st.title("📋 주간 회의 및 개선사항 관리")
    tm1,tm2,tm3 = st.tabs(["🚀 진행중 안건","➕ 신규 등록","🔍 이력 및 인쇄"])
    ws_mtg = SH.get('meetings')

    with tm1:
        ff2=st.radio("공장", ["전체","1공장","2공장","공통"], horizontal=True)
        if not df_meetings.empty:
            dm=df_meetings[df_meetings['상태']!='완료'].copy()
            if ff2!="전체" and '공장' in dm.columns: dm=dm[dm['공장']==ff2]
            if not dm.empty:
                ec=[c for c in ['ID','작성일','공장','안건내용','담당자','상태','비고'] if c in dm.columns]
                edited_m=st.data_editor(dm[ec], use_container_width=True, hide_index=True)
                if st.button("💾 저장"):
                    if ws_mtg:
                        all_r=ws_mtg.get_all_records(); hd=ws_mtg.row_values(1)
                        ed_dict={str(r.get('ID','')): r for _,r in edited_m.iterrows()}
                        upd=[]
                        for r in all_r:
                            rid=str(r.get('ID',''))
                            if rid in ed_dict:
                                er=ed_dict[rid]
                                upd.append([er.get('ID',r.get('ID','')),er.get('작성일',r.get('작성일','')),
                                            er.get('공장',r.get('공장','')),er.get('안건내용',r.get('안건내용','')),
                                            er.get('담당자',r.get('담당자','')),er.get('상태',r.get('상태','')),
                                            er.get('비고',r.get('비고',''))])
                            else:
                                upd.append([r.get(h,'') for h in hd])
                        ws_mtg.clear(); ws_mtg.update([hd]+upd)
                        st.success("저장됨"); st.cache_data.clear(); st.rerun()
            else: st.info("진행중 안건 없음")
        else: st.info("데이터 없음")

    with tm2:
        with st.form("new_mtg"):
            nd=st.date_input("날짜"); nf=st.selectbox("공장",["1공장","2공장","공통"])
            nc=st.text_area("안건 내용"); na=st.text_input("담당자")
            if st.form_submit_button("등록"):
                if not nc.strip(): st.error("내용을 입력하세요.")
                elif ws_mtg:
                    ws_mtg.append_row([f"M-{int(time.time())}",nd.strftime('%Y-%m-%d'),nf,nc,na,"진행중",""])
                    st.success("등록됨"); st.cache_data.clear(); st.rerun()

    with tm3:
        if not df_meetings.empty:
            st.dataframe(df_meetings, use_container_width=True, hide_index=True)
            mr=""
            for _,r in df_meetings.iterrows():
                sc="#d4edda" if r.get('상태','')=='완료' else "#fff3cd"
                mr+=f"<tr style='background:{sc}'><td>{r.get('작성일','')}</td><td>{r.get('공장','')}</td><td>{r.get('안건내용','')}</td><td>{r.get('담당자','')}</td><td>{r.get('상태','')}</td><td>{r.get('비고','')}</td></tr>"
            hm=f"<h2>회의 안건 이력</h2><table border='1' style='width:100%;border-collapse:collapse'><tr style='background:#ccc'><th>작성일</th><th>공장</th><th>안건내용</th><th>담당자</th><th>상태</th><th>비고</th></tr>{mr}</table>"
            st.components.v1.html(print_btn(hm,"회의이력","landscape"), height=55)
        else: st.info("데이터 없음")
