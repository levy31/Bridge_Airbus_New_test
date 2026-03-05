import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import google.generativeai as genai
import os
from datetime import datetime
import numpy as np
import requests

# --- 1. CONFIGURATION ---
st.set_page_config(layout="wide", page_title="Airbus Audit Master", initial_sidebar_state="auto")
st.title("🎯 IA Prototype for Bids costing competitiveness analysis (Bridges, ...) ")

# Lecture des clés API depuis les secrets
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", None)
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", None)

# Diagnostic des clés
st.sidebar.markdown("---")
st.sidebar.caption("🔑 **API Keys status**")
st.sidebar.write(f"Gemini: {'✅' if GEMINI_API_KEY else '❌'} {GEMINI_API_KEY[:4] if GEMINI_API_KEY else ''}...")
st.sidebar.write(f"Groq: {'✅' if GROQ_API_KEY else '❌'} {GROQ_API_KEY[:4] if GROQ_API_KEY else ''}...")

if not GEMINI_API_KEY and not GROQ_API_KEY:
    st.error("No API keys found. Please configure at least one provider in secrets.")
    st.stop()

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- 2. FONCTION D'APPEL GROQ ---
def call_groq_api(prompt):
    if not GROQ_API_KEY:
        return None
    try:
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
            "temperature": 0.7
        }
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data, timeout=60)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            st.error(f"Groq error {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Groq exception: {e}")
        return None

# ========== INITIALISATION DE L'INDEX D'ONGLET COURANT ==========
if "current_tab" not in st.session_state:
    st.session_state.current_tab = 0  # premier onglet par défaut

# ========== PLACEHOLDERS DANS LA SIDEBAR POUR LA PROGRESSION ==========
st.sidebar.header("📋 Analysis Workflow")
progress_bar_placeholder = st.sidebar.empty()
steps_placeholder = st.sidebar.empty()
st.sidebar.markdown("---")  # Séparateur

# --- 3. INITIALISATION DES DATES EN SESSION STATE ---
if "dates" not in st.session_state:
    st.session_state.dates = {
        "Devis_Alpha": datetime(2026, 6, 10).date(),
        "Devis_Beta": datetime(2024, 3, 20).date(),
        "Devis_Gamma": datetime(2022, 1, 15).date()
    }

# --- 4. SIDEBAR : DATES ET UPLOAD DES FICHIERS ---
st.sidebar.header("📅 Issue Dates")
files_list = ["Devis_Alpha", "Devis_Beta", "Devis_Gamma"]
for f in files_list:
    new_date = st.sidebar.date_input(
        f"Date for {f}",
        value=st.session_state.dates[f],
        key=f"date_input_{f}"
    )
    if new_date != st.session_state.dates[f]:
        st.session_state.dates[f] = new_date
dates = st.session_state.dates

# --- 5. UPLOAD DES FICHIERS ---
st.sidebar.header("📂 Upload Devis Files")
if "uploaded_alpha" not in st.session_state:
    st.session_state.uploaded_alpha = None
if "uploaded_beta" not in st.session_state:
    st.session_state.uploaded_beta = None
if "uploaded_gamma" not in st.session_state:
    st.session_state.uploaded_gamma = None

uploaded_alpha = st.sidebar.file_uploader("Devis_Alpha.xlsx", type=["xlsx"], key="alpha_upload")
uploaded_beta = st.sidebar.file_uploader("Devis_Beta.xlsx", type=["xlsx"], key="beta_upload")
uploaded_gamma = st.sidebar.file_uploader("Devis_Gamma.xlsx", type=["xlsx"], key="gamma_upload")

if uploaded_alpha is not None:
    st.session_state.uploaded_alpha = pd.read_excel(uploaded_alpha, engine='openpyxl', header=None)
if uploaded_beta is not None:
    st.session_state.uploaded_beta = pd.read_excel(uploaded_beta, engine='openpyxl', header=None)
if uploaded_gamma is not None:
    st.session_state.uploaded_gamma = pd.read_excel(uploaded_gamma, engine='openpyxl', header=None)

if st.sidebar.button("🔄 Reset uploads"):
    for k in ["uploaded_alpha", "uploaded_beta", "uploaded_gamma"]:
        st.session_state[k] = None
    st.rerun()

# --- 6. PARSING DES FICHIERS ---
def parse_complex_devis(df, system_name):
    data = df.iloc[3:].copy()
    # Nettoyer les colonnes WBS
    data[0] = data[0].astype(str).str.strip()
    data[1] = data[1].astype(str).str.strip()
    data[2] = data[2].astype(str).str.strip()
    data[3] = data[3].astype(str).str.strip()
    
    result = pd.DataFrame()
    result['WBS_2'] = data[0]
    result['WBS_3'] = data[1]
    result['WBS_4'] = data[2]
    result['WBS_5'] = data[3]
    result['Cost_Type'] = data[5]
    result['Siglum'] = data[6]
    result['Cost_Base'] = pd.to_numeric(data[7], errors='coerce')
    result['Input_Unit'] = data[9]
    
    mois_cols = [i for i in range(11, 59)]
    for col in mois_cols:
        data[col] = pd.to_numeric(data[col], errors='coerce').fillna(0)
    result['Cout_Total'] = data[mois_cols].sum(axis=1).round(2)
    
    # Calcul heures et taux pour Labour
    result['Heures'] = np.nan
    result['Taux_Horaire'] = np.nan
    mask_labour = result['Cost_Type'] == 'Labour'
    valid_mask = mask_labour & (result['Cost_Base'] > 0)
    result.loc[valid_mask, 'Taux_Horaire'] = result.loc[valid_mask, 'Cost_Base']
    result.loc[valid_mask, 'Heures'] = (result.loc[valid_mask, 'Cout_Total'] * 1000 / result.loc[valid_mask, 'Cost_Base']).round(0)
    
    result['System'] = system_name
    return result

# --- 7. CHARGEMENT DES DEVIS ---
raw_dfs = {}
for f_name in files_list:
    if f_name == "Devis_Alpha":
        df = st.session_state.uploaded_alpha if st.session_state.uploaded_alpha is not None else None
        if df is None and os.path.exists("Devis_Alpha.xlsx"):
            df = pd.read_excel("Devis_Alpha.xlsx", engine='openpyxl', header=None)
    elif f_name == "Devis_Beta":
        df = st.session_state.uploaded_beta if st.session_state.uploaded_beta is not None else None
        if df is None and os.path.exists("Devis_Beta.xlsx"):
            df = pd.read_excel("Devis_Beta.xlsx", engine='openpyxl', header=None)
    else:
        df = st.session_state.uploaded_gamma if st.session_state.uploaded_gamma is not None else None
        if df is None and os.path.exists("Devis_Gamma.xlsx"):
            df = pd.read_excel("Devis_Gamma.xlsx", engine='openpyxl', header=None)
    
    if df is not None:
        raw_dfs[f_name] = parse_complex_devis(df, f_name)
    else:
        st.warning(f"File {f_name} not found.")

if not raw_dfs:
    st.warning("Please upload or place the three Devis files.")
    st.stop()

# ========== ORGANISATION PAR ONGLETS ==========
tabs = st.tabs([
    "1- configure data",
    "2- graph analysis",
    "3- Drift analysis",
    "4- competitiveness deep dive",
    "5- IA Analysis",
    "✅ Validation"
])

# --- ONGLET 1 : configure data ---
with tabs[0]:
    st.session_state.current_tab = 0
    st.divider()

    # Déterminer l'ordre chronologique des systèmes à partir des dates
    chrono_systems = sorted(dates.keys(), key=lambda x: dates[x])  # ['Devis_Gamma', 'Devis_Beta', 'Devis_Alpha']

    # Calculer les totaux par WBS_2 et par système pour le graphique à barres
    global_view_data = []
    for name, df in raw_dfs.items():
        df_sys = df.copy()
        df_sys['System'] = name
        grouped = df_sys.groupby('WBS_2').agg({'Cout_Total': 'sum'}).reset_index()
        grouped['System'] = name
        global_view_data.append(grouped)
    df_global_view = pd.concat(global_view_data, ignore_index=True)

    # --- GRAPHIQUE LINÉAIRE (Total Global Cost per Bid) en premier ---
    st.subheader("Total Global Cost per Bid (chronological order)")
    total_global = df_global_view.groupby('System')['Cout_Total'].sum().reset_index()
    total_global['Date'] = total_global['System'].map(dates)
    total_global = total_global.sort_values('Date')

    fig_total = px.line(
        total_global,
        x='Date',
        y='Cout_Total',
        markers=True,
        title="Evolution of Total Raw Cost",
        text='System'
    )
    fig_total.update_traces(textposition='top center')
    fig_total.update_layout(xaxis_title="Date", yaxis_title="Total Cost (k€)")
    st.plotly_chart(fig_total, use_container_width=True)

    st.divider()
    st.subheader("Global View - WBS Level 1 (Sum of all Work Packages)")
    # --- GRAPHIQUE À BARRES (par WBS_2) ensuite ---
    fig_global = px.bar(
        df_global_view,
        x='WBS_2',
        y='Cout_Total',
        color='System',
        barmode='group',
        title="Total Raw Costs by WBS Level 1 (all sub-WPs included)",
        category_orders={"System": chrono_systems}
    )
    fig_global.update_xaxes(tickangle=45)
    st.plotly_chart(fig_global, use_container_width=True)
    st.caption("This chart shows the sum of all underlying Work Packages for each Level 1 WBS code, across the three bids.")

    st.divider()

    # --- Suite de l'onglet (sélection du niveau, mapping, tableaux) ---
    st.subheader("WBS Level Selection")
    level_choice = st.selectbox(
        "Select WBS level for analysis",
        ["Level 1 (WBS_2)", "Level 2 (WBS_3)", "Level 3 (WBS_4)", "Level 4 (WBS_5)"],
        index=3,
        key="level_select"
    )
    # Stocker dans session_state pour les autres onglets
    st.session_state.level_choice = level_choice

    level_map = {
        "Level 1 (WBS_2)": ("WBS_2", 1),
        "Level 2 (WBS_3)": ("WBS_3", 2),
        "Level 3 (WBS_4)": ("WBS_4", 3),
        "Level 4 (WBS_5)": ("WBS_5", 4)
    }
    level_col, level_num = level_map[level_choice]

    st.subheader("Technical Normalization Matrix")

    # Construction de la liste des WP pour le niveau choisi
    wp_list = []
    for name, df in raw_dfs.items():
        codes = df[level_col].unique()
        for code in codes:
            wp_list.append({"System": name, "Original WP": code})
    df_init = pd.DataFrame(wp_list).drop_duplicates()

    # Gestion du mapping
    MAPPING_FILE = "mapping_hierarchique.csv"

    if os.path.exists(MAPPING_FILE):
        df_mapping_all = pd.read_csv(MAPPING_FILE)
        required_cols = ['System', 'Original WP', 'Level']
        if not all(col in df_mapping_all.columns for col in required_cols):
            st.warning("The mapping file is corrupted or from an older version. Resetting.")
            df_mapping_all = pd.DataFrame()
            df_mapping_level = pd.DataFrame()
        else:
            df_mapping_level = df_mapping_all[df_mapping_all['Level'] == level_num].copy()
    else:
        df_mapping_all = pd.DataFrame()
        df_mapping_level = pd.DataFrame()

    if not df_mapping_level.empty:
        df_mapping = pd.merge(df_init, df_mapping_level, on=['System', 'Original WP'], how='left')
    else:
        df_mapping = df_init.copy()
        df_mapping['Common Name'] = ""
        df_mapping['Complexity'] = 1.0
        df_mapping['Comments'] = ""

    for col in ["Common Name", "Complexity", "Comments"]:
        if col not in df_mapping.columns:
            df_mapping[col] = "" if col != "Complexity" else 1.0
        else:
            if col == "Complexity":
                df_mapping[col] = df_mapping[col].fillna(1.0)
            else:
                df_mapping[col] = df_mapping[col].fillna("")

    edited_mapping = st.data_editor(df_mapping[['System', 'Original WP', 'Common Name', 'Complexity', 'Comments']],
                                    hide_index=True, width='stretch', use_container_width=True)

    if not edited_mapping.equals(df_mapping[['System', 'Original WP', 'Common Name', 'Complexity', 'Comments']]):
        edited_mapping['Level'] = level_num
        if not df_mapping_all.empty:
            df_mapping_all = df_mapping_all[df_mapping_all['Level'] != level_num]
        else:
            df_mapping_all = pd.DataFrame()
        df_mapping_all = pd.concat([df_mapping_all, edited_mapping], ignore_index=True)
        df_mapping_all.to_csv(MAPPING_FILE, index=False)
        st.success(f"Mapping saved for level {level_num}")
        st.rerun()

    map_dict = {(r["System"], r["Original WP"]): r["Common Name"] for _, r in edited_mapping.iterrows()}
    comp_dict = {(r["System"], r["Original WP"]): r["Complexity"] for _, r in edited_mapping.iterrows()}

    # Agrégation des données par niveau
    all_rows = []
    for name, df in raw_dfs.items():
        df['Date'] = pd.to_datetime(dates[name])
        grouped = df.groupby([level_col, 'Date']).agg({
            'Cout_Total': 'sum',
            'Heures': 'sum',
            'Taux_Horaire': 'mean',
        }).reset_index()
        grouped['System'] = name
        grouped.rename(columns={level_col: 'Code'}, inplace=True)
        all_rows.append(grouped)

    df_global = pd.concat(all_rows, ignore_index=True).sort_values('Date')

    # Ajouter le nom commun et la complexité
    df_global['Common_Name'] = df_global.apply(lambda row: map_dict.get((row['System'], row['Code']), row['Code']), axis=1)
    df_global['Complexity'] = df_global.apply(lambda row: comp_dict.get((row['System'], row['Code']), 1.0), axis=1)
    df_global['Normalized_Cost'] = df_global['Cout_Total'] / df_global['Complexity']

    # Créer des étiquettes uniques pour l'affichage
    df_global['Common_Name'] = df_global['Common_Name'].fillna('(empty)')
    name_counts = df_global.groupby('Common_Name')['Code'].nunique()
    def make_unique_label(row):
        name = row['Common_Name']
        if name_counts[name] > 1:
            return f"{name} ({row['Code']})"
        else:
            return name
    df_global['Unique_Label'] = df_global.apply(make_unique_label, axis=1)
    df_global['Display_Name'] = df_global['Common_Name'].apply(lambda x: x[:30] + '...' if len(x) > 30 else x)

    # --- TABLEAUX RÉCAPITULATIFS ---
    st.divider()
    st.subheader("Aggregated Data per Work Package")

    def create_summary_table(value_col):
        pivot = df_global.pivot_table(index='Code', columns='System', values=value_col, aggfunc='sum').fillna(0)
        names = df_global.groupby('Code')['Common_Name'].first()
        pivot = pivot.join(names)
        cols = ['Common_Name'] + [c for c in pivot.columns if c != 'Common_Name']
        pivot = pivot[cols]
        pivot.index.name = 'Code'
        return pivot

    st.write("**Raw Costs (k€)**")
    raw_table = create_summary_table('Cout_Total')
    num_cols = [c for c in raw_table.columns if c != 'Common_Name']
    styled_raw = raw_table.style.format("{:.2f}", subset=num_cols)
    st.dataframe(styled_raw, use_container_width=True)

    st.write("**Normalized Costs (k€)**")
    norm_table = create_summary_table('Normalized_Cost')
    styled_norm = norm_table.style.format("{:.2f}", subset=num_cols)
    st.dataframe(styled_norm, use_container_width=True)

    if df_global['Heures'].sum() > 0:
        st.write("**Hours**")
        hours_table = create_summary_table('Heures')
        styled_hours = hours_table.style.format("{:.0f}", subset=num_cols)
        st.dataframe(styled_hours, use_container_width=True)

    if df_global['Taux_Horaire'].notna().any():
        st.write("**Average Hourly Rates (€/h)**")
        rates_table = create_summary_table('Taux_Horaire')
        styled_rates = rates_table.style.format("{:.2f}", subset=num_cols)
        st.dataframe(styled_rates, use_container_width=True)

    # Stocker df_global dans session_state pour les autres onglets
    st.session_state.df_global = df_global
    st.session_state.pivot_raw_code = df_global.pivot_table(index='Code', columns='System', values='Cout_Total', aggfunc='sum').fillna(0)
    st.session_state.pivot_norm_code = df_global.pivot_table(index='Code', columns='System', values='Normalized_Cost', aggfunc='sum').fillna(0)
    st.session_state.code_to_unique = df_global.groupby('Code')['Unique_Label'].first().to_dict()
    st.session_state.chrono_order = df_global[['System', 'Date']].drop_duplicates().sort_values('Date')['System'].tolist()
    st.session_state.wp_drift_dict = None
    st.session_state.decomposition_data = None

def draw_bridge(pivot_df, base_sys, target_sys):
    if base_sys not in pivot_df.columns or target_sys not in pivot_df.columns:
        return go.Figure()
    v_base = pivot_df[base_sys].sum()
    labels = [base_sys]
    values = [v_base]
    measures = ["absolute"]
    for wp in pivot_df.index:
        diff = pivot_df.loc[wp, target_sys] - pivot_df.loc[wp, base_sys]
        if abs(diff) > 0.1:
            labels.append(wp)
            values.append(diff)
            measures.append("relative")
    labels.append(f"Total {target_sys}")
    values.append(pivot_df[target_sys].sum())
    measures.append("total")
    return go.Figure(go.Waterfall(measure=measures, x=labels, y=values)).update_layout(title=f"Bridge: {base_sys} → {target_sys}")

# --- ONGLET 2 : graph analysis ---
with tabs[1]:
    st.session_state.current_tab = 1
    st.divider()

    if 'df_global' not in st.session_state:
        st.warning("Please configure the level and mapping in the first tab first.")
    else:
        df_global = st.session_state.df_global
        pivot_raw_code = st.session_state.pivot_raw_code
        pivot_norm_code = st.session_state.pivot_norm_code
        code_to_unique = st.session_state.code_to_unique
        files_list = ["Devis_Alpha", "Devis_Beta", "Devis_Gamma"]

        unique_labels = df_global['Unique_Label'].dropna().unique().tolist()
        unique_labels.sort()

        st.subheader("Select Work Packages to display")
        if "selected_wps" not in st.session_state:
            st.session_state.selected_wps = unique_labels[:5] if len(unique_labels) > 5 else unique_labels

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Select All"):
                st.session_state.selected_wps = unique_labels.copy()
                st.rerun()
        with col2:
            if st.button("Clear All"):
                st.session_state.selected_wps = []
                st.rerun()
        with col3:
            st.caption(f"{len(st.session_state.selected_wps)} WP selected")

        # Affichage simple en 5 colonnes
        st.markdown("---")
        cols_per_row = 5
        current_selection = st.session_state.selected_wps.copy()
        new_selection = current_selection.copy()

        for i in range(0, len(unique_labels), cols_per_row):
            cols = st.columns(cols_per_row)
            for j, label in enumerate(unique_labels[i:i+cols_per_row]):
                with cols[j]:
                    key = f"cb_{label}"
                    checked = label in current_selection
                    if st.checkbox(label, value=checked, key=key):
                        if label not in new_selection:
                            new_selection.append(label)
                    else:
                        if label in new_selection:
                            new_selection.remove(label)

        if set(new_selection) != set(current_selection):
            st.session_state.selected_wps = new_selection
            st.rerun()

        # Filtrer les données
        df_filtered = df_global[df_global['Unique_Label'].isin(st.session_state.selected_wps)]

        st.subheader("Raw Data")
        if not df_filtered.empty:
            fig_raw_bar = px.bar(df_filtered, x="Unique_Label", y="Cout_Total", color="System", barmode="group", title="Raw Volume")
            fig_raw_bar.update_xaxes(tickangle=45)
            st.plotly_chart(fig_raw_bar, use_container_width=True, key="raw_vol")

            fig_raw_line = px.line(df_filtered, x="Date", y="Cout_Total", color="Unique_Label", markers=True, title="Raw Timeline")
            st.plotly_chart(fig_raw_line, use_container_width=True, key="raw_time")
        else:
            st.info("No data for selected Work Packages.")

        st.subheader("Normalized Data")
        if not df_filtered.empty:
            fig_norm_bar = px.bar(df_filtered, x="Unique_Label", y="Normalized_Cost", color="System", barmode="group", title="Normalized Volume")
            fig_norm_bar.update_xaxes(tickangle=45)
            st.plotly_chart(fig_norm_bar, use_container_width=True, key="norm_vol")

            fig_norm_line = px.line(df_filtered, x="Date", y="Normalized_Cost", color="Unique_Label", markers=True, title="Normalized Timeline")
            st.plotly_chart(fig_norm_line, use_container_width=True, key="norm_time")
        else:
            st.info("No data for selected Work Packages.")

        # Bridges
        st.subheader("Bridges")
        st.markdown("**Raw Bridge**")
        pivot_raw_unique = pivot_raw_code.rename(index=code_to_unique)
        col_a, col_b = st.columns(2)
        with col_a:
            base_r = st.selectbox("Base (Raw)", files_list, index=0, key="base_raw")
        with col_b:
            target_r = st.selectbox("Target (Raw)", [s for s in files_list if s != base_r], index=0, key="target_raw")
        if base_r != target_r:
            st.plotly_chart(draw_bridge(pivot_raw_unique, base_r, target_r), use_container_width=True, key="raw_bridge")
        else:
            st.warning("Choose two different systems")

        st.markdown("**Normalized Bridge**")
        pivot_norm_unique = pivot_norm_code.rename(index=code_to_unique)
        col_c, col_d = st.columns(2)
        with col_c:
            base_n = st.selectbox("Base (Normalized)", files_list, index=0, key="base_norm")
        with col_d:
            target_n = st.selectbox("Target (Normalized)", [s for s in files_list if s != base_n], index=0, key="target_norm")
        if base_n != target_n:
            st.plotly_chart(draw_bridge(pivot_norm_unique, base_n, target_n), use_container_width=True, key="norm_bridge")
        else:
            st.warning("Choose two different systems")

# --- ONGLET 3 : Drift analysis ---
with tabs[2]:
    st.session_state.current_tab = 2
    st.divider()
    if 'df_global' not in st.session_state:
        st.warning("Please configure the level and mapping in the first tab first.")
    else:
        df_global = st.session_state.df_global
        code_to_unique = st.session_state.code_to_unique

        st.subheader("Global Drift (Total Normalized Cost)")
        total_norm = df_global.groupby('System')['Normalized_Cost'].sum().reset_index()
        total_norm = total_norm.merge(df_global[['System','Date']].drop_duplicates(), on='System').sort_values('Date')
        if len(total_norm) >= 2:
            x = (total_norm['Date'] - total_norm['Date'].min()).dt.days.values
            y = total_norm['Normalized_Cost'].values
            try:
                coeffs = np.polyfit(x, y, 1)
                slope_day = coeffs[0]
                first_cost = y[0]
                annual_pct = (slope_day * 365 / first_cost) * 100 if first_cost else 0
            except np.linalg.LinAlgError:
                slope_day = 0
                annual_pct = 0
                st.warning("Could not compute global drift (SVD error).")
            slope_month = slope_day * 30.44
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=total_norm['Date'], y=total_norm['Normalized_Cost'], mode='markers+lines', name='Total'))
            fig.add_trace(go.Scatter(x=[total_norm['Date'].min(), total_norm['Date'].max()], 
                                     y=np.polyval([slope_day, coeffs[1] if 'coeffs' in locals() else 0], [0, x.max()]), 
                                     mode='lines', line=dict(dash='dash', color='red'), name='Trend'))
            fig.update_layout(title="Total Normalized Cost Over Time", xaxis_title="Date", yaxis_title="€")
            st.plotly_chart(fig, use_container_width=True, key="global_drift")
            col1, col2, col3 = st.columns(3)
            col1.metric("Daily drift", f"{slope_day:+.2f} €/day")
            col2.metric("Monthly drift", f"{slope_month:+.2f} €/month")
            col3.metric("Annualized drift", f"{annual_pct:+.1f} %/year")
        else:
            st.warning("Not enough points for global drift")
            slope_day = 0
            annual_pct = 0

        st.divider()
        st.subheader("Per Work Package Drift")
        wp_drift_dict = {}
        for code in df_global['Code'].unique():
            sub = df_global[df_global['Code'] == code].sort_values('Date')
            display_name = code_to_unique.get(code, code)
            if len(sub) >= 2:
                x = (sub['Date'] - sub['Date'].min()).dt.days.values
                y = sub['Normalized_Cost'].values
                if np.all(y == y[0]):
                    slope_day = 0
                    annual = 0
                else:
                    try:
                        coeffs = np.polyfit(x, y, 1)
                        slope_day = coeffs[0]
                        first = y[0]
                        annual = (slope_day * 365 / first) * 100 if first != 0 else 0
                    except np.linalg.LinAlgError:
                        slope_day = 0
                        annual = 0
                wp_drift_dict[code] = {'pente': slope_day, 'annual': annual, 'data': sub, 'display': display_name}
            else:
                wp_drift_dict[code] = {'pente': None, 'annual': None, 'data': sub, 'display': display_name}
        st.session_state.wp_drift_dict = wp_drift_dict

        wp_list = []
        for code, vals in wp_drift_dict.items():
            if vals['pente'] is not None:
                wp_list.append({'Work Package': vals['display'], 'Annualized drift (%)': round(vals['annual'], 1)})
        if wp_list:
            df_drift = pd.DataFrame(wp_list)
            st.dataframe(df_drift, use_container_width=True, hide_index=True)
        else:
            st.info("No WP with enough data to compute drift slopes (need at least two points).")

        if wp_drift_dict:
            options = [vals['display'] for vals in wp_drift_dict.values()]
            selected_display = st.selectbox("Select a Work Package for detailed view", options, key="wp_selector")
            selected_code = None
            for code, vals in wp_drift_dict.items():
                if vals['display'] == selected_display:
                    selected_code = code
                    break
            if selected_code:
                sub = wp_drift_dict[selected_code]['data']
                display_name = wp_drift_dict[selected_code]['display']
                if not sub.empty:
                    fig = px.line(sub, x='Date', y='Normalized_Cost', text='System', markers=True, title=f"{display_name} normalized cost")
                    fig.update_traces(textposition='top center')
                    st.plotly_chart(fig, use_container_width=True, key="wp_detail")
                else:
                    st.warning("No data for this Work Package.")

# --- ONGLET 4 : competitiveness deep dive ---
with tabs[3]:
    st.session_state.current_tab = 3
    st.divider()
    if 'df_global' not in st.session_state:
        st.warning("Please configure the level and mapping in the first tab first.")
    else:
        df_global = st.session_state.df_global
        code_to_unique = st.session_state.code_to_unique

        st.subheader("Rate vs Technical Competitiveness Analysis")
        if 'Heures' in df_global.columns and 'Taux_Horaire' in df_global.columns:
            if df_global['Heures'].notna().any() and df_global['Taux_Horaire'].notna().any():
                decomposition_data = []
                for code in df_global['Code'].unique():
                    sub = df_global[df_global['Code'] == code].sort_values('Date')
                    if len(sub) >= 2:
                        first = sub.iloc[0]
                        last = sub.iloc[-1]
                        if first['Heures'] > 0 and first['Taux_Horaire'] > 0 and last['Heures'] > 0 and last['Taux_Horaire'] > 0:
                            cout_first = first['Heures'] * first['Taux_Horaire'] / 1000
                            cout_last = last['Heures'] * last['Taux_Horaire'] / 1000
                            var_totale = ((cout_last - cout_first) / cout_first) * 100
                            effect_rate = ((last['Taux_Horaire'] - first['Taux_Horaire']) * first['Heures'] / 1000) / cout_first * 100
                            effect_hours = ((last['Heures'] - first['Heures']) * first['Taux_Horaire'] / 1000) / cout_first * 100
                            effect_cross = var_totale - effect_rate - effect_hours
                            if abs(var_totale) > 0.01:
                                share_rate = (effect_rate / var_totale) * 100
                                share_hours = (effect_hours / var_totale) * 100
                                share_cross = (effect_cross / var_totale) * 100
                            else:
                                share_rate = share_hours = share_cross = 0.0
                            if share_hours > 60:
                                interp = "🔴 Strong loss (hours dominate)"
                            elif share_hours > 30:
                                interp = "🟡 Moderate loss"
                            elif share_hours < -30:
                                interp = "🟢 Gain"
                            else:
                                interp = "⚪ Mainly rate-driven"
                            decomposition_data.append({
                                'Code': code,
                                'WP': code_to_unique.get(code, code),
                                'Total Δ%': f"{var_totale:+.1f}%",
                                'Rate share': f"{share_rate:+.0f}%",
                                'Hours share': f"{share_hours:+.0f}%",
                                'Cross share': f"{share_cross:+.0f}%",
                                'Interpretation': interp
                            })
                if decomposition_data:
                    st.session_state.decomposition_data = decomposition_data
                    df_display = pd.DataFrame(decomposition_data)[['WP', 'Total Δ%', 'Rate share', 'Hours share', 'Cross share', 'Interpretation']]
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                    df_plot = pd.DataFrame(decomposition_data)
                    fig = go.Figure()
                    fig.add_trace(go.Bar(name='Rate share', x=df_plot['WP'], y=[float(s.replace('%','').replace('+','')) for s in df_plot['Rate share']], marker_color='lightblue'))
                    fig.add_trace(go.Bar(name='Hours share', x=df_plot['WP'], y=[float(s.replace('%','').replace('+','')) for s in df_plot['Hours share']], marker_color='lightcoral'))
                    fig.add_trace(go.Bar(name='Cross share', x=df_plot['WP'], y=[float(s.replace('%','').replace('+','')) for s in df_plot['Cross share']], marker_color='lightgreen'))
                    fig.update_layout(barmode='stack', title="Contribution shares to total cost variation", yaxis_title="% of total variation")
                    st.plotly_chart(fig, use_container_width=True, key="compet_chart")
                else:
                    st.info("Not enough data for decomposition (no WP with varying hours/rates).")
            else:
                st.info("Hourly rate data not available for any WP.")
        else:
            st.info("Hourly rate data not available.")

# --- ONGLET 5 : IA Analysis ---
with tabs[4]:
    st.session_state.current_tab = 4
    st.divider()
    if 'df_global' not in st.session_state:
        st.warning("Please configure the level and mapping in the first tab first.")
    else:
        df_global = st.session_state.df_global
        pivot_norm_code = st.session_state.pivot_norm_code
        chrono_order = st.session_state.chrono_order
        code_to_unique = st.session_state.code_to_unique
        wp_drift_dict = st.session_state.get('wp_drift_dict', {})
        decomposition_data = st.session_state.get('decomposition_data', [])

        st.subheader("IA Analysis of Observed Drifts")
        provider_opts = []
        if GEMINI_API_KEY:
            provider_opts.append("Gemini")
        if GROQ_API_KEY:
            provider_opts.append("Groq")
        if not provider_opts:
            st.error("No IA provider available")
        else:
            provider = st.radio("Choose IA provider", provider_opts, horizontal=True, key="ia_provider")

            total_norm = df_global.groupby('System')['Normalized_Cost'].sum().reset_index()
            total_norm = total_norm.merge(df_global[['System','Date']].drop_duplicates(), on='System').sort_values('Date')
            if len(total_norm) >= 2:
                x = (total_norm['Date'] - total_norm['Date'].min()).dt.days.values
                y = total_norm['Normalized_Cost'].values
                try:
                    coeffs = np.polyfit(x, y, 1)
                    slope_day = coeffs[0]
                    first_cost = y[0]
                    annual_pct = (slope_day * 365 / first_cost) * 100 if first_cost else 0
                    global_trend = f"global slope = {slope_day:.2f} €/day, i.e. {annual_pct:.1f}% per year (initial total cost = {first_cost:.2f} €)"
                except np.linalg.LinAlgError:
                    global_trend = "Global trend not available (SVD error)"
            else:
                global_trend = "Global trend not available"

            wp_trends = []
            for code, vals in wp_drift_dict.items():
                if vals['pente'] is not None:
                    display_name = vals['display']
                    wp_trends.append(f"- {display_name}: annualized drift = {vals['annual']:.1f}%")
            wp_trends_str = "\n".join(wp_trends) if wp_trends else "No per-WP trends"

            decomp_summary = ""
            if decomposition_data:
                decomp_summary = "\n\n**Rate vs Hours decomposition:**\n"
                for d in decomposition_data:
                    decomp_summary += f"- {d['WP']}: Total Δ = {d['Total Δ%']}, Rate share = {d['Rate share']}, Hours share = {d['Hours share']} → {d['Interpretation']}\n"

            prompt = f"""
You are an expert in aerospace project analysis. We have calculated normalized cost drifts for three versions (Alpha, Beta, Gamma) with their actual dates.

**Global trend**:
{global_trend}

**Per Work Package trends**:
{wp_trends_str}
{decomp_summary}

Questions:
1. Which Work Packages show the strongest upward drift? Which are stable or decreasing?
2. Is the global drift concerning compared to normal inflation (2-3% per year)?
3. Based on the decomposition, which WPs are losing technical competitiveness (hours share > 60%)?
4. What strategic recommendations would you make?

Answer concisely.
"""
            if st.button("🤖 Analyze Drifts", key="btn_drift"):
                with st.spinner(f"IA ({provider}) analyzing..."):
                    if provider == "Gemini":
                        try:
                            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                            model_name = next((m for m in available_models if "flash" in m), available_models[0])
                            model = genai.GenerativeModel(model_name)
                            response = model.generate_content(prompt)
                            st.session_state.ai_result = response.text
                        except Exception as e:
                            st.error(f"Gemini error: {e}")
                    else:
                        response_text = call_groq_api(prompt)
                        if response_text:
                            st.session_state.ai_result = response_text
            if 'ai_result' in st.session_state:
                st.markdown(st.session_state.ai_result)

            st.divider()
            if st.button("🧠 Full Strategic Audit", key="btn_audit"):
                pivot_norm_unique = pivot_norm_code.rename(index=code_to_unique)
                summary = f"""
ACTUAL PROJECT DATA:
- Chronological order: {' -> '.join(chrono_order)}
- Dates: { {k: v.strftime('%Y-%m-%d') for k,v in dates.items()} }
- Analysis level: {st.session_state.level_choice}
- Normalized costs per WP:
{pivot_norm_unique.to_string()}
"""
                prompt_full = f"""
Act as a Senior Airbus Project Controller. Analyze the cost drift based on the real data below.
{summary}
Provide a concise audit covering:
1. Key Work Packages with significant cost variations.
2. Impact of chronological sequence on budget stability.
3. Strategic recommendations.
"""
                with st.spinner("Audit in progress..."):
                    if provider == "Gemini":
                        try:
                            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                            model_name = next((m for m in available_models if "flash" in m), available_models[0])
                            model = genai.GenerativeModel(model_name)
                            response = model.generate_content(prompt_full)
                            st.session_state.ai_audit = response.text
                        except Exception as e:
                            st.error(f"Gemini error: {e}")
                    else:
                        response_text = call_groq_api(prompt_full)
                        if response_text:
                            st.session_state.ai_audit = response_text
            if 'ai_audit' in st.session_state:
                st.info(st.session_state.ai_audit)

# --- ONGLET 6 : Validation ---
with tabs[5]:
    # NE PAS MODIFIER current_tab POUR GARDER LA PROGRESSION
    st.divider()
    st.sidebar.header("🧪 Validation Mode")
    oracle_file = st.sidebar.file_uploader("Load oracle file", type=["xlsx", "csv"], key="oracle_upload")
    if oracle_file:
        st.info("Oracle loaded – comparison would appear here.")
    else:
        st.info("Load an oracle file to validate results.")

# ========== MISE À JOUR DE LA PROGRESSION DANS LA SIDEBAR ==========
# Calcul de la progression basée sur l'onglet courant (max 5 étapes)
current_step = st.session_state.current_tab + 1
progress_pct = (current_step / 5) * 100

progress_bar_placeholder.progress(progress_pct / 100, text=f"Overall progress: {int(progress_pct)}%")

# Affichage des étapes
step_status = []
for i in range(5):
    if i < st.session_state.current_tab:
        step_status.append("✅")
    elif i == st.session_state.current_tab:
        step_status.append("👉")
    else:
        step_status.append("⬜")

step_labels = [
    "1. configure data",
    "2. graph analysis",
    "3. Drift analysis",
    "4. competitiveness deep dive",
    "5. IA Analysis"
]

steps_text = ""
for i in range(5):
    steps_text += f"{step_status[i]} **{step_labels[i]}**\n\n"
steps_placeholder.markdown(steps_text)

# --- GUIDE ---
st.divider()
st.subheader("📚 Audit Guide")
st.markdown("""
* **configure data**: Choose WBS level, view a global overview of Level 1 costs, edit mapping, and view aggregated data per Work Package.
* **graph analysis**: Raw and normalized cost views, with bridge charts. Select WPs via checkboxes (Select All/Clear All).
* **Drift analysis**: Global trend and per-WP annualized drift (calculated across all systems). No system column, just the Work Package.
* **competitiveness deep dive**: Decomposition of cost variations into rate (inflation) and hours (technical) effects.
* **IA Analysis**: AI commentary on drifts.
* **Validation**: Compare against an oracle file.
""")