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
st.set_page_config(layout="wide", page_title="Airbus Audit Master",
                   initial_sidebar_state="auto")

st.title("🎯 IA Prototype for Bids costing competitiveness analysis (Bridges, ...)")

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
def clean_wbs_code(val):
    if pd.isna(val):
        return np.nan
    s = str(val).strip()
    if s == '' or s.lower() in ['nan', 'none', 'null']:
        return np.nan
    return s

def parse_complex_devis(df, system_name):
    data = df.iloc[3:].copy()
    data[0] = data[0].apply(clean_wbs_code)
    data[1] = data[1].apply(clean_wbs_code)
    data[2] = data[2].apply(clean_wbs_code)
    data[3] = data[3].apply(clean_wbs_code)

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

# ========== CONFIGURATION (HORS ONGLETS) ==========
with st.expander("⚙️ Configuration - WBS Level & Normalization Matrix", expanded=True):
    st.subheader("WBS Level Selection")
    level_choice = st.selectbox(
        "Select WBS level for analysis",
        ["Level 1 (WBS_2)", "Level 2 (WBS_3)", "Level 3 (WBS_4)", "Level 4 (WBS_5)"],
        index=3,
        key="level_select"
    )
    st.session_state.level_choice = level_choice

    level_map = {
        "Level 1 (WBS_2)": ("WBS_2", 1),
        "Level 2 (WBS_3)": ("WBS_3", 2),
        "Level 3 (WBS_4)": ("WBS_4", 3),
        "Level 4 (WBS_5)": ("WBS_5", 4)
    }
    level_col, level_num = level_map[level_choice]

    # Construction de la liste des WP pour le niveau choisi
    wp_list = []
    for name, df in raw_dfs.items():
        codes = df[level_col].dropna().unique()
        for code in codes:
            wp_list.append({"System": name, "Original WP": code})
    df_init = pd.DataFrame(wp_list).drop_duplicates()

    # Gestion du mapping
    MAPPING_FILE = "mapping_hierarchique.csv"
    if os.path.exists(MAPPING_FILE):
        df_mapping_all = pd.read_csv(MAPPING_FILE)
        required_cols = ['System', 'Original WP', 'Level']
        if not all(col in df_mapping_all.columns for col in required_cols):
            st.warning("The mapping file is corrupted. Resetting.")
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

    # Détection des groupes naturels (même nom original)
    df_mapping['Orig'] = df_mapping['Original WP'].astype(str)
    orig_groups = df_mapping.groupby('Orig')['System'].apply(list).reset_index()
    orig_groups['Count'] = orig_groups['System'].apply(len)
    orig_groups['Systems'] = orig_groups['System'].apply(lambda x: ', '.join(x))

    natural_groups = orig_groups[orig_groups['Count'] > 1].copy()
    isolated = orig_groups[orig_groups['Count'] == 1].copy()

    # --- Construction des dataframes pour l'édition ---
    # Dictionnaire des complexités existantes par (système, code)
    comp_dict_existing = {(row['System'], row['Original WP']): row['Complexity'] 
                          for _, row in df_mapping.iterrows()}

    # Pour les groupes naturels : on prépare un dataframe avec une colonne par système
    natural_edit_rows = []
    for _, row in natural_groups.iterrows():
        orig = row['Orig']
        systems_list = row['System']  # liste des systèmes
        # Récupérer le common name et comments (on prend la première occurrence, elles sont censées être identiques)
        sub = df_mapping[df_mapping['Orig'] == orig]
        common_name = sub['Common Name'].iloc[0] if not sub['Common Name'].isna().all() else ""
        comments = sub['Comments'].iloc[0] if not sub['Comments'].isna().all() else ""
        
        base_row = {
            'Original WP (group)': orig,
            'Systems': ', '.join(systems_list),
            'Common Name': common_name,
            'Comments': comments
        }
        # Ajouter une colonne pour chaque système avec la complexité associée (1.0 par défaut)
        for sys in files_list:
            if sys in systems_list:
                base_row[f'Complexity {sys}'] = comp_dict_existing.get((sys, orig), 1.0)
            else:
                base_row[f'Complexity {sys}'] = None
        natural_edit_rows.append(base_row)
    
    df_natural_edit = pd.DataFrame(natural_edit_rows) if natural_edit_rows else pd.DataFrame()

    # Pour les isolés : on garde le format simple
    isolated_edit_rows = []
    for _, row in isolated.iterrows():
        orig = row['Orig']
        sys = row['System'][0]
        sub = df_mapping[df_mapping['Orig'] == orig]
        common_name = sub['Common Name'].iloc[0] if not sub['Common Name'].isna().all() else ""
        comments = sub['Comments'].iloc[0] if not sub['Comments'].isna().all() else ""
        complexity = comp_dict_existing.get((sys, orig), 1.0)
        isolated_edit_rows.append({
            'System': sys,
            'Original WP': orig,
            'Common Name': common_name,
            'Complexity': complexity,
            'Comments': comments
        })
    df_isolated_edit = pd.DataFrame(isolated_edit_rows) if isolated_edit_rows else pd.DataFrame()

    # --- Édition des groupes naturels ---
    st.markdown("#### 🔗 Natural groups (same code appears in multiple systems)")
    st.caption("You can define a common name and comments for the group, as well as a complexity **per system**. Columns for non‑applicable systems are ignored.")

    if not df_natural_edit.empty:
        column_config = {
            'Original WP (group)': st.column_config.TextColumn('Original WP (group)', disabled=True),
            'Systems': st.column_config.TextColumn('Systems', disabled=True),
            'Common Name': st.column_config.TextColumn('Common Name'),
            'Comments': st.column_config.TextColumn('Comments'),
        }
        for sys in files_list:
            col_name = f'Complexity {sys}'
            column_config[col_name] = st.column_config.NumberColumn(
                f'Complexity {sys}',
                min_value=0.1,
                max_value=10.0,
                step=0.1,
                format="%.1f",
                help=f"Normalization factor for {sys} (default 1.0). Leave empty if not applicable."
            )
        
        edited_natural = st.data_editor(
            df_natural_edit,
            column_config=column_config,
            hide_index=True,
            width='stretch',
            use_container_width=True,
            key="mapping_natural"
        )
    else:
        edited_natural = pd.DataFrame()
        st.info("No natural groups found.")

    # --- Édition des isolés ---
    st.markdown("#### 🔍 Isolated work packages (unique codes)")
    if not df_isolated_edit.empty:
        edited_isolated = st.data_editor(
            df_isolated_edit[['System', 'Original WP', 'Common Name', 'Complexity', 'Comments']],
            column_config={
                'System': st.column_config.TextColumn('System', disabled=True),
                'Original WP': st.column_config.TextColumn('Original WP', disabled=True),
                'Common Name': st.column_config.TextColumn('Common Name'),
                'Complexity': st.column_config.NumberColumn('Complexity', min_value=0.1, max_value=10.0, step=0.1, format="%.1f"),
                'Comments': st.column_config.TextColumn('Comments')
            },
            hide_index=True,
            width='stretch',
            use_container_width=True,
            key="mapping_isolated"
        )
    else:
        edited_isolated = pd.DataFrame()
        st.info("No isolated work packages.")

    # Bouton pour appliquer la configuration
    if st.button("💾 Apply Configuration", type="primary"):
        new_mapping_rows = []

        # Traitement des groupes naturels
        if not edited_natural.empty:
            for _, row in edited_natural.iterrows():
                orig_group = row['Original WP (group)']
                common = row['Common Name'] if pd.notna(row['Common Name']) else ""
                comments = row['Comments'] if pd.notna(row['Comments']) else ""
                for sys in files_list:
                    comp_val = row.get(f'Complexity {sys}')
                    if pd.notna(comp_val) and comp_val is not None:
                        new_mapping_rows.append({
                            'System': sys,
                            'Original WP': orig_group,
                            'Common Name': common,
                            'Complexity': float(comp_val),
                            'Comments': comments
                        })

        # Traitement des isolés
        if not edited_isolated.empty:
            for _, row in edited_isolated.iterrows():
                new_mapping_rows.append({
                    'System': row['System'],
                    'Original WP': row['Original WP'],
                    'Common Name': row['Common Name'] if pd.notna(row['Common Name']) else "",
                    'Complexity': float(row['Complexity']) if pd.notna(row['Complexity']) else 1.0,
                    'Comments': row['Comments'] if pd.notna(row['Comments']) else ""
                })

        new_mapping = pd.DataFrame(new_mapping_rows)

        if not new_mapping.empty:
            cols_compare = ['System', 'Original WP', 'Common Name', 'Complexity', 'Comments']
            df_current = df_mapping[cols_compare].reset_index(drop=True) if not df_mapping.empty else pd.DataFrame(columns=cols_compare)
            df_new = new_mapping[cols_compare].reset_index(drop=True)
            
            if not df_current.equals(df_new):
                new_mapping['Level'] = level_num
                if not df_mapping_all.empty:
                    df_mapping_all = df_mapping_all[df_mapping_all['Level'] != level_num]
                else:
                    df_mapping_all = pd.DataFrame()
                df_mapping_all = pd.concat([df_mapping_all, new_mapping], ignore_index=True)
                df_mapping_all.to_csv(MAPPING_FILE, index=False)
                st.success(f"Mapping saved for level {level_num}")

                # Mise à jour des dictionnaires et agrégation
                map_dict = {(r["System"], r["Original WP"]): r["Common Name"] for _, r in new_mapping.iterrows()}
                comp_dict = {(r["System"], r["Original WP"]): r["Complexity"] for _, r in new_mapping.iterrows()}

                # Agrégation des données au niveau code
                all_rows = []
                for name, df in raw_dfs.items():
                    df['Date'] = pd.to_datetime(dates[name])
                    df_filtered = df[df[level_col].notna()].copy()
                    if df_filtered.empty:
                        continue
                    grouped = df_filtered.groupby([level_col, 'Date']).agg({
                        'Cout_Total': 'sum',
                        'Heures': 'sum',
                        'Taux_Horaire': 'mean',
                    }).reset_index()
                    grouped['System'] = name
                    grouped.rename(columns={level_col: 'Code'}, inplace=True)
                    all_rows.append(grouped)

                if not all_rows:
                    st.error(f"No data found for the selected level {level_choice}. Please check your files.")
                    st.stop()

                df_global = pd.concat(all_rows, ignore_index=True).sort_values('Date')
                df_global['Common_Name'] = df_global.apply(
                    lambda row: map_dict.get((row['System'], row['Code']), row['Code'])
                    if map_dict.get((row['System'], row['Code'])) != ""
                    else row['Code'],
                    axis=1
                )
                df_global['Complexity'] = df_global.apply(lambda row: comp_dict.get((row['System'], row['Code']), 1.0), axis=1)
                df_global['Normalized_Cost'] = df_global['Cout_Total'] / df_global['Complexity']

                # Agrégation par Common Name
                df_common = df_global.groupby(['Common_Name', 'System', 'Date']).agg({
                    'Cout_Total': 'sum',
                    'Normalized_Cost': 'sum',
                    'Heures': 'sum',
                    'Taux_Horaire': 'mean',
                    'Code': lambda x: list(x)
                }).reset_index()

                pivot_raw_common = df_common.pivot_table(index='Common_Name', columns='System', values='Cout_Total', aggfunc='sum').fillna(0)
                pivot_norm_common = df_common.pivot_table(index='Common_Name', columns='System', values='Normalized_Cost', aggfunc='sum').fillna(0)

                st.session_state.df_global = df_global
                st.session_state.df_common = df_common
                st.session_state.pivot_raw_common = pivot_raw_common
                st.session_state.pivot_norm_common = pivot_norm_common
                st.session_state.pivot_raw_code = df_global.pivot_table(index='Code', columns='System', values='Cout_Total', aggfunc='sum').fillna(0)
                st.session_state.pivot_norm_code = df_global.pivot_table(index='Code', columns='System', values='Normalized_Cost', aggfunc='sum').fillna(0)
                st.session_state.code_to_unique = df_global.groupby('Code')['Common_Name'].first().to_dict()
                st.session_state.common_to_codes = df_global.groupby('Common_Name')['Code'].apply(list).to_dict()
                st.session_state.chrono_order = df_global[['System', 'Date']].drop_duplicates().sort_values('Date')['System'].tolist()
                st.session_state.wp_drift_dict = None
                st.session_state.decomposition_data = None

                st.rerun()
            else:
                st.info("No changes detected.")
        else:
            st.warning("No mapping data to save.")

    if 'df_common' in st.session_state:
        with st.expander("📊 Current groups by Common Name"):
            df_temp = st.session_state.df_common.copy()
            groups = df_temp.groupby('Common_Name').agg({
                'System': lambda x: list(set(x)),
                'Code': lambda x: list(set([item for sublist in x for item in sublist]))
            }).reset_index()
            groups['WP List'] = groups.apply(lambda row: ', '.join([f"{s}:{c}" for s,c in zip(row['System'], row['Code'])]), axis=1)
            groups = groups[['Common_Name', 'WP List']]
            st.dataframe(groups, use_container_width=True, hide_index=True)

# ========== FONCTIONS UTILITAIRES ==========
def format_cost_value(value, decimals=1):
    if pd.isna(value):
        return ""
    if abs(value) >= 1000:
        return f"{value/1000:.{decimals}f} M€"
    else:
        return f"{value:.{decimals}f} k€"

def draw_bridge(pivot_df, base_sys, target_sys, title_prefix=""):
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

    max_val = max(abs(v) for v in values)
    unit = "M€" if max_val >= 1000 else "k€"
    factor = 1000 if unit == "M€" else 1
    values_adjusted = [v/factor for v in values]

    fig = go.Figure(go.Waterfall(
        measure=measures,
        x=labels,
        y=values_adjusted,
        text=[f"{v:.1f} {unit}" for v in values_adjusted],
        textposition="outside"
    ))
    fig.update_layout(title=f"{title_prefix}Bridge: {base_sys} → {target_sys}", yaxis_title=f"Cost ({unit})")
    fig.update_yaxes(tickformat=".1f")
    return fig

def build_wbs_hierarchy(raw_dfs, system=None):
    hierarchy_rows = []
    for sys_name, df in raw_dfs.items():
        if system and sys_name != system:
            continue
        df_sys = df.copy()
        df_sys = df_sys.dropna(subset=['WBS_2', 'WBS_3', 'WBS_4', 'WBS_5'], how='all')
        seen_nodes = set()
        for _, row in df_sys.iterrows():
            wbs_2 = row['WBS_2']
            wbs_3 = row['WBS_3']
            wbs_4 = row['WBS_4']
            wbs_5 = row['WBS_5']

            if pd.notna(wbs_2):
                node_id = f"{sys_name}_L1_{wbs_2}"
                if node_id not in seen_nodes:
                    seen_nodes.add(node_id)
                    hierarchy_rows.append({
                        'id': node_id, 'parent': '', 'name': str(wbs_2), 'level': 1,
                        'system': sys_name, 'path': str(wbs_2)
                    })
            if pd.notna(wbs_3):
                node_id = f"{sys_name}_L2_{wbs_2}_{wbs_3}"
                if node_id not in seen_nodes:
                    seen_nodes.add(node_id)
                    hierarchy_rows.append({
                        'id': node_id, 'parent': f"{sys_name}_L1_{wbs_2}", 'name': str(wbs_3), 'level': 2,
                        'system': sys_name, 'path': f"{wbs_2} / {wbs_3}"
                    })
            if pd.notna(wbs_4):
                node_id = f"{sys_name}_L3_{wbs_2}_{wbs_3}_{wbs_4}"
                if node_id not in seen_nodes:
                    seen_nodes.add(node_id)
                    hierarchy_rows.append({
                        'id': node_id, 'parent': f"{sys_name}_L2_{wbs_2}_{wbs_3}", 'name': str(wbs_4), 'level': 3,
                        'system': sys_name, 'path': f"{wbs_2} / {wbs_3} / {wbs_4}"
                    })
            if pd.notna(wbs_5):
                node_id = f"{sys_name}_L4_{wbs_2}_{wbs_3}_{wbs_4}_{wbs_5}"
                if node_id not in seen_nodes:
                    seen_nodes.add(node_id)
                    hierarchy_rows.append({
                        'id': node_id, 'parent': f"{sys_name}_L3_{wbs_2}_{wbs_3}_{wbs_4}", 'name': str(wbs_5), 'level': 4,
                        'system': sys_name, 'path': f"{wbs_2} / {wbs_3} / {wbs_4} / {wbs_5}"
                    })
    return pd.DataFrame(hierarchy_rows)

# ========== FONCTION POUR AFFICHER LE CADRE HOT TOPICS ==========
def display_hot_topic(title, points):
    """Display a styled box with key insights."""
    if not points:
        return
    with st.container():
        st.info(f"🔥 **{title}**\n\n" + "\n".join([f"- {p}" for p in points]))

# ========== INITIALISATION DU TYPE DE COÛT PAR DÉFAUT ==========
if "cost_type" not in st.session_state:
    st.session_state.cost_type = "Normalized"  # or "Raw"

# ========== ORGANISATION PAR ONGLETS ==========
tabs = st.tabs([
    "🌳 WBS Structure",
    "1- Input Data",
    "2- Global Analysis",
    "3- WP analysis",
    "4- Bridges",
    "5- Drift analysis",
    "6- competitiveness deep dive",
    "7- IA Analysis",
    "✅ Validation"
])

# --- ONGLET 0 : WBS Structure ---
with tabs[0]:
    st.subheader("🌳 Work Breakdown Structure (WBS) - Hierarchical View")
    st.caption("This shows the structure of work packages across all bids. No costs are displayed, only the hierarchy.")
    
    # Hot Topics for WBS Structure
    hot_points = []
    if raw_dfs:
        total_wp = sum(len(df[level_col].dropna().unique()) for df in raw_dfs.values())
        hot_points.append(f"🧩 **{total_wp}** work packages across all bids at level {level_choice}.")
        hot_points.append(f"📅 Bids: {', '.join([f'{sys} ({dates[sys].strftime('%d/%m/%Y')})' for sys in files_list])}.")
    else:
        hot_points.append("❌ No files loaded. Please upload the three Devis files.")
    display_hot_topic("Hot Topics - WBS Structure", hot_points)
    
    if not raw_dfs:
        st.warning("Please upload files first.")
    else:
        selected_system = st.selectbox("Select system to display (or 'All')", options=["All"] + files_list, index=0, key="wbs_structure_system")
        with st.spinner("Building WBS structure..."):
            system_param = None if selected_system == "All" else selected_system
            df_hierarchy = build_wbs_hierarchy(raw_dfs, system_param)
            if df_hierarchy.empty:
                st.warning("No hierarchical structure found.")
            else:
                df_hierarchy['dummy_value'] = 1
                fig = px.treemap(
                    df_hierarchy, ids='id', parents='parent', names='name', values='dummy_value',
                    title=f"WBS Structure - {selected_system if selected_system != 'All' else 'All bids'}",
                    color='level', color_continuous_scale='Blues',
                    hover_data={'path': True, 'level': True, 'system': True}
                )
                fig.update_traces(
                    textinfo="label",
                    hovertemplate='<b>%{label}</b><br>Level: %{customdata[1]}<br>System: %{customdata[2]}<br>Path: %{customdata[0]}<extra></extra>',
                    customdata=df_hierarchy[['path', 'level', 'system']].values
                )
                fig.update_layout(height=700, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)
                with st.expander("📋 Show hierarchical table"):
                    df_display = df_hierarchy.copy()
                    df_display['indent'] = df_display['level'].apply(lambda x: ' ' * (x-1) + '└─ ')
                    df_display['display_name'] = df_display.apply(lambda row: f"{row['indent']}{row['name']} (L{row['level']})", axis=1)
                    df_display = df_display[['system', 'level', 'path', 'display_name']].sort_values(['system', 'path'])
                    st.dataframe(df_display, use_container_width=True, hide_index=True)

# --- ONGLET 1 : Input Data ---
with tabs[1]:
    if 'df_common' not in st.session_state:
        st.warning("Please apply the configuration first (click 'Apply Configuration' above).")
    else:
        cost_type = st.radio("Cost type", ["Raw", "Normalized"], horizontal=True, key="cost_type_input_data")
        st.session_state.cost_type = cost_type
        
        df_common = st.session_state.df_common
        level_choice = st.session_state.level_choice
        chrono_systems = sorted(files_list, key=lambda x: dates[x])
        system_display_names = {sys: f"{sys} ({dates[sys].strftime('%Y-%m-%d')})" for sys in chrono_systems}

        # Hot Topics for Input Data - Focus sur l'évolution temporelle
        hot_points = []
        col_cost = 'Cout_Total' if cost_type == 'Raw' else 'Normalized_Cost'
        
        # Nombre de lots analysés
        nb_lots = df_common['Common_Name'].nunique()
        hot_points.append(f"📦 **{nb_lots}** work packages (Common Names) analysed.")
        
        # Coûts par devis (pas de somme globale)
        costs_by_bid = df_common.groupby('System')[col_cost].sum()
        for system in files_list:
            if system in costs_by_bid.index:
                hot_points.append(f"💰 {system} ({dates[system].strftime('%d/%m/%Y')}): **{format_cost_value(costs_by_bid[system])}**.")
        
        # Évolution entre premier et dernier devis
        if len(chrono_systems) >= 2:
            first_sys = chrono_systems[0]
            last_sys = chrono_systems[-1]
            first_cost = costs_by_bid.get(first_sys, 0)
            last_cost = costs_by_bid.get(last_sys, 0)
            if first_cost > 0:
                var_pct = ((last_cost - first_cost) / first_cost) * 100
                delta_days = (dates[last_sys] - dates[first_sys]).days
                years = delta_days / 365.25
                var_annual = var_pct / years if years > 0 else 0
                hot_points.append(f"📊 Evolution {first_sys} → {last_sys}: **{var_pct:+.1f}%** ({var_annual:+.1f}%/year over {years:.1f} years).")
        
        # Heures (si disponibles) - par devis, pas de somme globale
        if df_common['Heures'].notna().any() and df_common['Heures'].sum() > 0:
            hours_by_bid = df_common.groupby('System')['Heures'].sum()
            hot_points.append("⏱️ **Hours per bid:**")
            for system in files_list:
                if system in hours_by_bid.index and hours_by_bid[system] > 0:
                    hot_points.append(f"   - {system}: **{hours_by_bid[system]:,.0f} h**")
        
        # Taux horaire moyen (si disponible) - par devis
        if df_common['Taux_Horaire'].notna().any():
            avg_rate = df_common[df_common['Taux_Horaire'] > 0].groupby('System')['Taux_Horaire'].mean()
            hot_points.append("💶 **Average hourly rate per bid:**")
            for system in files_list:
                if system in avg_rate.index and not pd.isna(avg_rate[system]):
                    hot_points.append(f"   - {system}: **{avg_rate[system]:.2f} €/h**")
        
        display_hot_topic("Hot Topics - Input Data", hot_points)

        def create_common_summary_table(value_col):
            pivot = df_common.pivot_table(index='Common_Name', columns='System', values=value_col, aggfunc='sum').fillna(0)
            pivot = pivot.reset_index()
            pivot.rename(columns=system_display_names, inplace=True)
            system_cols = [system_display_names[sys] for sys in chrono_systems]
            cols = ['Common_Name'] + system_cols
            pivot = pivot[cols]
            return pivot, system_cols

        def add_total_row(pivot, num_cols):
            pivot_with_total = pivot.copy()
            total_values = {col: pivot_with_total[col].sum() for col in num_cols}
            total_values['Common_Name'] = 'TOTAL'
            total_df = pd.DataFrame([total_values])
            return pd.concat([pivot_with_total, total_df], ignore_index=True)

        def format_cost_dataframe(pivot, num_cols):
            df_display = pivot.copy()
            for col in num_cols:
                df_display[col] = df_display[col].apply(lambda x: format_cost_value(x, 1))
            return df_display

        st.subheader(f"Aggregated Data per Common Name - {level_choice}")

        if cost_type == "Raw":
            st.write("**Raw Costs**")
            raw_table, num_cols_raw = create_common_summary_table('Cout_Total')
            raw_table_with_total = add_total_row(raw_table, num_cols_raw)
            raw_table_display = format_cost_dataframe(raw_table_with_total, num_cols_raw)
            st.dataframe(raw_table_display, use_container_width=True, hide_index=True)
        else:
            st.write("**Normalized Costs**")
            norm_table, num_cols_norm = create_common_summary_table('Normalized_Cost')
            norm_table_with_total = add_total_row(norm_table, num_cols_norm)
            norm_table_display = format_cost_dataframe(norm_table_with_total, num_cols_norm)
            st.dataframe(norm_table_display, use_container_width=True, hide_index=True)

        if df_common['Heures'].sum() > 0:
            st.write("**Hours**")
            hours_table, num_cols_hours = create_common_summary_table('Heures')
            hours_table_with_total = add_total_row(hours_table, num_cols_hours)
            hours_display = hours_table_with_total.copy()
            for col in num_cols_hours:
                hours_display[col] = hours_display[col].apply(lambda x: f"{x:.0f} h")
            st.dataframe(hours_display, use_container_width=True, hide_index=True)

        if df_common['Taux_Horaire'].notna().any():
            st.write("**Average Hourly Rates**")
            rates_table, num_cols_rates = create_common_summary_table('Taux_Horaire')
            rates_display = rates_table.copy()
            for col in num_cols_rates:
                rates_display[col] = rates_display[col].apply(lambda x: f"{x:.2f} €/h")
            st.dataframe(rates_display, use_container_width=True, hide_index=True)

        with st.expander("📋 Show detailed view by original code"):
            df_code = st.session_state.df_global
            col_value = 'Cout_Total' if cost_type == 'Raw' else 'Normalized_Cost'
            code_pivot = df_code.pivot_table(index='Code', columns='System', values=col_value, aggfunc='sum').fillna(0).reset_index()
            code_pivot.rename(columns=system_display_names, inplace=True)
            st.dataframe(code_pivot, use_container_width=True, hide_index=True)

# --- ONGLET 2 : Global Analysis ---
with tabs[2]:
    if 'df_global' not in st.session_state:
        st.warning("Please apply the configuration first.")
    else:
        cost_type = st.radio("Cost type", ["Raw", "Normalized"], horizontal=True, key="cost_type_global")
        st.session_state.cost_type = cost_type
        
        # Hot Topics for Global Analysis
        hot_points = []
        col_cost = 'Cout_Total' if cost_type == 'Raw' else 'Normalized_Cost'
        
        # Coûts par devis
        total_by_system = st.session_state.df_common.groupby('System')[col_cost].sum().sort_values()
        if len(total_by_system) >= 2:
            min_sys = total_by_system.idxmin()
            max_sys = total_by_system.idxmax()
            hot_points.append(f"📈 Highest bid: **{max_sys}** ({format_cost_value(total_by_system[max_sys])})")
            hot_points.append(f"📉 Lowest bid: **{min_sys}** ({format_cost_value(total_by_system[min_sys])})")
        
        # Variation globale
        total_by_date = st.session_state.df_common.groupby(['System', 'Date'])[col_cost].sum().reset_index()
        total_by_date = total_by_date.sort_values('Date')
        if len(total_by_date) >= 2:
            first = total_by_date.iloc[0]
            last = total_by_date.iloc[-1]
            var_pct = ((last[col_cost] - first[col_cost]) / first[col_cost]) * 100
            delta_days = (last['Date'] - first['Date']).days
            years = delta_days / 365.25
            var_annual = var_pct / years if years > 0 else 0
            hot_points.append(f"📊 Total variation: **{var_pct:+.1f}%** ({var_annual:+.1f}%/year over {years:.1f} years).")
        
        display_hot_topic("Hot Topics - Global Analysis", hot_points)
        
        st.subheader(f"Global Analysis ({cost_type} Costs)")

        # Évolution du total
        chrono_systems = sorted(dates.keys(), key=lambda x: dates[x])
        total = st.session_state.df_common.groupby('System')[col_cost].sum().reset_index()
        total['Date'] = total['System'].map(dates)
        total = total.sort_values('Date')

        fig_total = px.line(total, x='Date', y=col_cost, markers=True,
                            title=f"Evolution of Total {cost_type} Cost", text='System')
        fig_total.update_traces(textposition='top center')
        max_val = total[col_cost].max()
        if max_val >= 1000:
            fig_total.update_traces(y=total[col_cost]/1000)
            fig_total.update_layout(yaxis_title="Cost (M€)")
        else:
            fig_total.update_layout(yaxis_title="Cost (k€)")
        fig_total.update_yaxes(tickformat=".1f")
        st.plotly_chart(fig_total, use_container_width=True, key="global_line")

        st.divider()

        # Bar chart par niveau WBS
        st.subheader(f"{cost_type} View - {level_choice} (Sum of all Work Packages)")
        overall_view_data = []
        for name, df in raw_dfs.items():
            df_sys = df.copy()
            df_sys['System'] = name
            df_filtered = df_sys[df_sys[level_col].notna()].copy()
            if df_filtered.empty:
                continue
            # On récupère les coûts depuis df_global pour ce système et ces codes
            costs = st.session_state.df_global[st.session_state.df_global['System'] == name]
            grouped = costs.groupby('Code')[col_cost].sum().reset_index()
            grouped['System'] = name
            grouped.rename(columns={'Code': level_col}, inplace=True)
            overall_view_data.append(grouped)

        if overall_view_data:
            df_overall_view = pd.concat(overall_view_data, ignore_index=True)
            fig_global = px.bar(df_overall_view, x=level_col, y=col_cost, color='System',
                                barmode='group',
                                title=f"Total {cost_type} Costs by {level_choice}",
                                category_orders={"System": chrono_systems},
                                labels={col_cost: "Cost", level_col: "WBS Code"})
            fig_global.update_xaxes(tickangle=45)
            max_val_bar = df_overall_view[col_cost].max()
            if max_val_bar >= 1000:
                fig_global.update_traces(y=df_overall_view[col_cost]/1000)
                fig_global.update_layout(yaxis_title="Cost (M€)")
            else:
                fig_global.update_layout(yaxis_title="Cost (k€)")
            fig_global.update_yaxes(tickformat=".1f")
            st.plotly_chart(fig_global, use_container_width=True, key="global_bar")
            st.caption(f"This chart shows the sum of {cost_type.lower()} costs for each {level_choice} code, across the three bids.")
        else:
            st.warning(f"No data for level {level_choice}")

# --- ONGLET 3 : WP analysis ---
with tabs[3]:
    if 'df_common' not in st.session_state:
        st.warning("Please apply the configuration first.")
    else:
        cost_type = st.radio("Cost type", ["Raw", "Normalized"], horizontal=True, key="cost_type_wp")
        st.session_state.cost_type = cost_type
        
        df_common = st.session_state.df_common
        common_names = sorted(df_common['Common_Name'].unique().tolist())
        col_cost = 'Cout_Total' if cost_type == 'Raw' else 'Normalized_Cost'

        # Hot Topics for WP analysis
        hot_points = []
        selected = st.session_state.get('selected_commons', [])
        if selected:
            df_sel = df_common[df_common['Common_Name'].isin(selected)]
            hot_points.append(f"✅ **{len(selected)}** work packages selected (out of {len(common_names)}).")
            
            # Coûts par devis pour les lots sélectionnés
            costs_by_bid = df_sel.groupby('System')[col_cost].sum()
            for system in files_list:
                if system in costs_by_bid.index:
                    hot_points.append(f"💰 {system}: **{format_cost_value(costs_by_bid[system])}**.")
            
            # Work package with largest variation between first and last bid
            if len(df_sel['Date'].unique()) >= 2:
                var_list = []
                for lot in selected:
                    sub = df_sel[df_sel['Common_Name'] == lot].sort_values('Date')
                    if len(sub) >= 2:
                        c_first = sub.iloc[0][col_cost]
                        c_last = sub.iloc[-1][col_cost]
                        if c_first != 0:
                            var = ((c_last - c_first) / c_first) * 100
                            var_list.append((lot, var))
                if var_list:
                    top_var = max(var_list, key=lambda x: abs(x[1]))
                    hot_points.append(f"📈 WP with largest variation: **{top_var[0]}** ({top_var[1]:+.1f}%).")
        else:
            hot_points.append("❌ No work package selected.")
        display_hot_topic("Hot Topics - WP Analysis", hot_points)

        st.subheader(f"Select Common Names to display ({cost_type} Costs)")

        # Initialisation de la sélection
        if "selected_commons" not in st.session_state:
            st.session_state.selected_commons = common_names[:5]

        # Boutons de sélection rapide
        c1, c2, c3 = st.columns(3)
        if c1.button("Select All", key="all_wp"):
            st.session_state.selected_commons = common_names
            st.rerun()
        if c2.button("Clear All", key="clear_wp"):
            st.session_state.selected_commons = []
            st.rerun()

        # Grille de cases à cocher
        cols_per_row = 5
        for i in range(0, len(common_names), cols_per_row):
            cols = st.columns(cols_per_row)
            for j, name in enumerate(common_names[i:i+cols_per_row]):
                with cols[j]:
                    if st.checkbox(name, value=(name in st.session_state.selected_commons), key=f"chk_{name}"):
                        if name not in st.session_state.selected_commons:
                            st.session_state.selected_commons.append(name)
                    else:
                        if name in st.session_state.selected_commons:
                            st.session_state.selected_commons.remove(name)

        # Filtrage des données
        df_filtered = df_common[df_common['Common_Name'].isin(st.session_state.selected_commons)]

        if df_filtered.empty:
            st.info("No data for selected Common Names.")
        else:
            # 1. Agrégation pour les barres
            df_bar = df_filtered.groupby(['Common_Name', 'System'], as_index=False)[col_cost].sum()
            chrono_systems = st.session_state.chrono_order

            # 2. Graphique à barres
            st.subheader(f"{cost_type} Data Analysis")
            max_val = df_bar[col_cost].max()
            unit = "M€" if max_val >= 1000 else "k€"
            factor = 1000 if unit == "M€" else 1
            df_plot = df_bar.copy()
            df_plot['Display_Cost'] = df_plot[col_cost] / factor
            fig_bar = px.bar(
                df_plot,
                x='Common_Name',
                y='Display_Cost',
                color='System',
                barmode='group',
                category_orders={"System": chrono_systems},
                title=f"{cost_type} Volume (Total per WP in {unit})",
                labels={"Display_Cost": f"Cost ({unit})", "Common_Name": "WP Name"}
            )
            st.plotly_chart(fig_bar, use_container_width=True)

            # 3. Tableau de vérification
            with st.expander("📋 Data Table", expanded=False):
                df_table_display = df_bar.pivot(index='Common_Name', columns='System', values=col_cost).fillna(0)
                df_table_display = df_table_display[[c for c in chrono_systems if c in df_table_display.columns]]
                st.write(f"**{cost_type} Costs per Bid (k€)**")
                st.dataframe(df_table_display.style.format("{:.1f} k€"), use_container_width=True)

            # 4. Timeline
            df_line = df_filtered.groupby(['Date', 'Common_Name'], as_index=False)[col_cost].sum()
            df_line['Display_Cost'] = df_line[col_cost] / factor
            fig_line = px.line(
                df_line, x='Date', y='Display_Cost', color='Common_Name', markers=True,
                title=f"Cost Evolution ({unit})",
                labels={"Display_Cost": f"Cost ({unit})"}
            )
            st.plotly_chart(fig_line, use_container_width=True)

# --- ONGLET 4 : Bridges ---
with tabs[4]:
    if 'pivot_norm_common' not in st.session_state or 'pivot_raw_common' not in st.session_state:
        st.warning("Please apply the configuration first.")
    else:
        cost_type = st.radio("Cost type", ["Raw", "Normalized"], horizontal=True, key="cost_type_bridge")
        st.session_state.cost_type = cost_type
        
        pivot = st.session_state.pivot_norm_common if cost_type == 'Normalized' else st.session_state.pivot_raw_common
        files_list = ["Devis_Alpha", "Devis_Beta", "Devis_Gamma"]

        # Hot Topics for Bridges
        hot_points = []
        base_n = st.session_state.get('base_bridge', files_list[0])
        target_n = st.session_state.get('target_bridge', files_list[1] if len(files_list)>1 else files_list[0])
        if base_n != target_n and base_n in pivot.columns and target_n in pivot.columns:
            total_base = pivot[base_n].sum()
            total_target = pivot[target_n].sum()
            diff = total_target - total_base
            var_pct = (diff / total_base) * 100 if total_base != 0 else 0
            hot_points.append(f"🔁 Difference **{base_n} → {target_n}**: {format_cost_value(diff)} ({var_pct:+.1f}%).")
            # Main contributing work packages
            contrib = (pivot[target_n] - pivot[base_n]).sort_values(ascending=False)
            top3_pos = contrib[contrib > 0].head(3)
            top3_neg = contrib[contrib < 0].tail(3)
            if not top3_pos.empty:
                hot_points.append(f"⬆️ Top increases: {', '.join([f'{wp} (+{format_cost_value(val)})' for wp, val in top3_pos.items()])}")
            if not top3_neg.empty:
                hot_points.append(f"⬇️ Top decreases: {', '.join([f'{wp} ({format_cost_value(val)})' for wp, val in top3_neg.items()])}")
        else:
            hot_points.append("🔀 Select two different bids.")
        display_hot_topic("Hot Topics - Bridges", hot_points)

        st.subheader(f"Bridge Charts - {cost_type} Cost Decomposition Between Bids")
        st.markdown("""
        Bridge charts show the step‑by‑step contribution of each Work Package (grouped by Common Name)
        to the total cost difference between two bids.

        Positive values (above zero) increase the total cost, negative values decrease it.
        """)

        col_a, col_b = st.columns(2)
        with col_a:
            base_n = st.selectbox("Base bid", files_list, index=0, key="base_bridge")
        with col_b:
            target_options_n = [s for s in files_list if s != base_n]
            target_n = st.selectbox("Target bid", target_options_n, index=0, key="target_bridge")

        if base_n != target_n:
            # Graphique Bridge
            st.plotly_chart(draw_bridge(pivot, base_n, target_n, f"{cost_type} "),
                            use_container_width=True, key="bridge")

            # --- Heatmap of annual variations for this pair ---
            st.markdown("---")
            st.subheader(f"Heatmap of average annual variations ({base_n} → {target_n})")
            st.markdown("""
            This heatmap shows the **average annual variation** (in %) of costs between the two selected bids.
            - **Red**: increase (> 0%)
            - **Blue**: decrease (< 0%)
            - Intensity is proportional to magnitude.
            Work packages missing data on either bid are ignored.
            """)

            date_base = dates[base_n]
            date_target = dates[target_n]
            delta_years = (date_target - date_base).days / 365.25
            if delta_years <= 0:
                st.warning("Duration between bids is zero or negative.")
            else:
                df_common_local = st.session_state.df_common
                col_val = 'Cout_Total' if cost_type == 'Raw' else 'Normalized_Cost'
                df_pair = df_common_local[df_common_local['System'].isin([base_n, target_n])]
                pivot_pair = df_pair.pivot_table(index='Common_Name', columns='System', values=col_val, aggfunc='sum').fillna(0)

                heat_data = []
                for common in pivot_pair.index:
                    c_base = pivot_pair.loc[common, base_n] if base_n in pivot_pair.columns else 0
                    c_target = pivot_pair.loc[common, target_n] if target_n in pivot_pair.columns else 0
                    if c_base != 0 and not np.isnan(c_base) and not np.isnan(c_target):
                        var_ann = ((c_target - c_base) / c_base) / delta_years * 100
                        heat_data.append({
                            'Common Name': common,
                            'Annual variation (%)': round(var_ann, 1)
                        })

                if heat_data:
                    df_heat = pd.DataFrame(heat_data).set_index('Common Name')
                    df_heat = df_heat.sort_values('Annual variation (%)', ascending=False)

                    fig_heat = go.Figure(data=go.Heatmap(
                        z=df_heat.values.reshape(-1, 1),
                        x=[f"{base_n} → {target_n}"],
                        y=df_heat.index,
                        colorscale='RdBu',
                        zmid=0,
                        text=df_heat.values,
                        texttemplate='%{text:.1f}',
                        textfont={"size":10},
                        colorbar_title="%/year",
                        hoverongaps=False
                    ))
                    fig_heat.update_layout(
                        title=f"Average annual variation ({base_n} → {target_n})",
                        xaxis_title="Period",
                        yaxis_title="Work Package",
                        height=max(400, 25*len(df_heat))
                    )
                    st.plotly_chart(fig_heat, use_container_width=True)
                else:
                    st.info("No common work packages with non‑zero costs to compute variation.")
        else:
            st.warning("Please choose two different systems to build a bridge.")

        with st.expander("ℹ️ About bridge charts"):
            st.markdown("""
            The bridge starts with the total cost of the base bid. 
            Each subsequent bar shows how much a specific Work Package (Common Name) adds or subtracts to reach the target bid total.
            The final bar shows the total cost of the target bid.
            """)

# --- ONGLET 5 : Drift analysis ---
with tabs[5]:
    if 'df_common' not in st.session_state:
        st.warning("Please apply the configuration first.")
    else:
        cost_type = st.radio("Cost type", ["Raw", "Normalized"], horizontal=True, key="cost_type_drift")
        st.session_state.cost_type = cost_type
        
        df_common = st.session_state.df_common
        col_cost = 'Cout_Total' if cost_type == 'Raw' else 'Normalized_Cost'

        # Hot Topics for Drift analysis
        hot_points = []
        total = df_common.groupby('System')[col_cost].sum().reset_index()
        total = total.merge(df_common[['System','Date']].drop_duplicates(), on='System').sort_values('Date')
        if len(total) >= 2:
            x = (total['Date'] - total['Date'].min()).dt.days.values
            y = total[col_cost].values
            try:
                coeffs = np.polyfit(x, y, 1)
                slope_day = coeffs[0]
                first_cost = y[0]
                annual_pct = (slope_day * 365 / first_cost) * 100 if first_cost else 0
                hot_points.append(f"📉 Global drift: **{annual_pct:+.1f}%/year**.")
            except:
                hot_points.append("📉 Global drift: could not compute.")
        
        # Work packages with highest drift
        wp_drift_dict = st.session_state.get('wp_drift_dict', {})
        if wp_drift_dict:
            drifts = [(common, vals['annual']) for common, vals in wp_drift_dict.items() if vals['annual'] is not None]
            drifts.sort(key=lambda x: x[1], reverse=True)
            top3_up = drifts[:3]
            top3_down = [d for d in drifts if d[1] < 0][-3:] if any(d[1] < 0 for d in drifts) else []
            if top3_up:
                hot_points.append(f"⬆️ Highest increases: {', '.join([f'{wp} (+{val:.1f}%/year)' for wp, val in top3_up])}")
            if top3_down:
                hot_points.append(f"⬇️ Highest decreases: {', '.join([f'{wp} ({val:.1f}%/year)' for wp, val in top3_down])}")
        display_hot_topic("Hot Topics - Drift", hot_points)

        st.subheader(f"Global Drift (Total {cost_type} Cost)")
        total = df_common.groupby('System')[col_cost].sum().reset_index()
        total = total.merge(df_common[['System','Date']].drop_duplicates(), on='System').sort_values('Date')

        if len(total) >= 2:
            x = (total['Date'] - total['Date'].min()).dt.days.values
            y = total[col_cost].values
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
            max_val = total[col_cost].max()
            unit = "M€" if max_val >= 1000 else "k€"
            factor = 1000 if unit == "M€" else 1
            y_adjusted = total[col_cost] / factor

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=total['Date'], y=y_adjusted, mode='markers+lines', name='Total',
                                      text=[f"{v:.1f}" for v in y_adjusted]))
            trend_y = np.polyval([slope_day/factor, coeffs[1]/factor if 'coeffs' in locals() else 0], [0, x.max()])
            fig.add_trace(go.Scatter(x=[total['Date'].min(), total['Date'].max()], y=trend_y,
                                     mode='lines', line=dict(dash='dash', color='red'), name='Trend'))
            fig.update_layout(title=f"Total {cost_type} Cost Over Time", xaxis_title="Date", yaxis_title=f"Cost ({unit})")
            fig.update_yaxes(tickformat=".1f")
            st.plotly_chart(fig, use_container_width=True, key="global_drift")

            col1, col2, col3 = st.columns(3)
            col1.metric("Daily drift", f"{slope_day:+.2f} k€/day")
            col2.metric("Monthly drift", f"{slope_month:+.2f} k€/month")
            col3.metric("Annualized drift", f"{annual_pct:+.1f} %/year")
        else:
            st.warning("Not enough points for global drift")

        st.divider()
        st.subheader(f"Per Work Package Drift (by Common Name) - {cost_type}")

        wp_drift_dict = {}
        for common in df_common['Common_Name'].unique():
            sub = df_common[df_common['Common_Name'] == common].sort_values('Date')
            if len(sub) >= 2:
                x = (sub['Date'] - sub['Date'].min()).dt.days.values
                y = sub[col_cost].values
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
                wp_drift_dict[common] = {'pente': slope_day, 'annual': annual, 'data': sub}
            else:
                wp_drift_dict[common] = {'pente': None, 'annual': None, 'data': sub}
        st.session_state.wp_drift_dict = wp_drift_dict

        wp_list = []
        for common, vals in wp_drift_dict.items():
            if vals['pente'] is not None:
                wp_list.append({'Work Package': common, 'Annualized drift (%)': round(vals['annual'], 1)})
        if wp_list:
            df_drift = pd.DataFrame(wp_list)
            st.dataframe(df_drift, use_container_width=True, hide_index=True)
        else:
            st.info("No WP with enough data to compute drift slopes (need at least two points).")

        if wp_drift_dict:
            options = [common for common in wp_drift_dict.keys()]
            selected_common = st.selectbox("Select a Work Package for detailed view", options, key="wp_selector")
            if selected_common:
                sub = wp_drift_dict[selected_common]['data']
                if not sub.empty:
                    max_val_wp = sub[col_cost].max()
                    unit_wp = "M€" if max_val_wp >= 1000 else "k€"
                    factor_wp = 1000 if unit_wp == "M€" else 1
                    y_wp = sub[col_cost] / factor_wp
                    fig = px.line(sub, x='Date', y=y_wp, text='System', markers=True,
                                  title=f"{selected_common} {cost_type} cost",
                                  labels={"y": f"Cost ({unit_wp})", "Date": "Date"})
                    fig.update_traces(textposition='top center')
                    fig.update_yaxes(tickformat=".1f")
                    st.plotly_chart(fig, use_container_width=True, key="wp_detail")
                else:
                    st.warning("No data for this Work Package.")

# --- ONGLET 6 : competitiveness deep dive ---
with tabs[6]:
    if 'df_common' not in st.session_state:
        st.warning("Please apply the configuration first.")
    else:
        cost_type = st.radio("Cost type for total", ["Raw", "Normalized"], horizontal=True, key="cost_type_compet")
        st.session_state.cost_type = cost_type
        
        df_common = st.session_state.df_common

        # Hot Topics for competitiveness
        hot_points = []
        if 'decomposition_data' in st.session_state and st.session_state.decomposition_data:
            decomp = st.session_state.decomposition_data
            if decomp:
                df_decomp = pd.DataFrame(decomp)
                if not df_decomp.empty and 'Interpretation' in df_decomp.columns:
                    emoji_series = df_decomp['Interpretation'].str.extract(r'([🔴🟠🟡🟢🔵⚪])')[0]
                    if not emoji_series.isna().all():
                        counts = emoji_series.value_counts()
                        for emoji, count in counts.items():
                            label = {
                                '🔴': 'very high increases',
                                '🟠': 'high increases',
                                '🟡': 'moderate increases',
                                '🟢': 'decreases',
                                '🔵': 'very high decreases',
                                '⚪': 'stable / within norm'
                            }.get(emoji, 'other')
                            hot_points.append(f"{emoji} **{count}** work packages with {label}.")
                    
                    if 'heures_pct' in df_decomp.columns and not df_decomp['heures_pct'].isna().all():
                        worst_hours_idx = df_decomp['heures_pct'].idxmax()
                        worst_hours = df_decomp.loc[worst_hours_idx] if pd.notna(worst_hours_idx) else None
                        if worst_hours is not None and worst_hours['heures_pct'] > 0:
                            hot_points.append(f"⏱️ Worst hours effect: **{worst_hours['Common Name']}** (+{worst_hours['heures_pct']:.1f}% of initial cost).")
                else:
                    hot_points.append("📊 Decomposition data available but format mismatch.")
            else:
                hot_points.append("📊 Decomposition data is empty.")
        else:
            hot_points.append("📊 Decomposition not yet computed. Click 'Apply Configuration' first.")
        
        if hot_points:
            display_hot_topic("Hot Topics - Competitiveness", hot_points)

        st.subheader("Rate vs Technical Competitiveness Analysis")
        st.caption(f"Analysis of cost variation between the first and last bid, decomposed into rate, hours and other cost effects. Percentages are relative to the **{cost_type.lower()} initial cost** of the work package. Coloured dots indicate the magnitude of the average annual variation.")

        if 'Heures' in df_common.columns and 'Taux_Horaire' in df_common.columns:
            if df_common['Heures'].notna().any() and df_common['Taux_Horaire'].notna().any():
                decomposition_data = []
                col_total = 'Cout_Total' if cost_type == 'Raw' else 'Normalized_Cost'
                
                for common in df_common['Common_Name'].unique():
                    sub = df_common[df_common['Common_Name'] == common].sort_values('Date')
                    
                    if len(sub) >= 2:
                        first = sub.iloc[0]
                        last = sub.iloc[-1]
                        
                        # Handle missing hours/rates
                        h1 = first['Heures'] if pd.notna(first['Heures']) else 0
                        r1 = first['Taux_Horaire'] if pd.notna(first['Taux_Horaire']) else 0
                        h2 = last['Heures'] if pd.notna(last['Heures']) else 0
                        r2 = last['Taux_Horaire'] if pd.notna(last['Taux_Horaire']) else 0
                        
                        C1 = first[col_total]
                        C2 = last[col_total]
                        
                        # Other costs (non‑labour)
                        labour_cost1 = (h1 * r1 / 1000) if h1>0 and r1>0 else 0
                        labour_cost2 = (h2 * r2 / 1000) if h2>0 and r2>0 else 0
                        other1 = C1 - labour_cost1
                        other2 = C2 - labour_cost2
                        
                        # Total variation in % of initial cost
                        var_pct = ((C2 - C1) / C1) * 100 if C1 != 0 else 0
                        
                        # Effects (exact additive decomposition) in absolute value
                        rate_effect_abs = (r2 - r1) * h1 / 1000
                        hours_effect_abs = (h2 - h1) * r2 / 1000
                        other_effect_abs = other2 - other1
                        
                        # As % of initial cost
                        rate_effect_pct = (rate_effect_abs / C1) * 100 if C1 != 0 else 0
                        hours_effect_pct = (hours_effect_abs / C1) * 100 if C1 != 0 else 0
                        other_effect_pct = (other_effect_abs / C1) * 100 if C1 != 0 else 0
                        
                        # Duration in years
                        delta_days = (last['Date'] - first['Date']).days
                        nb_ans = delta_days / 365.25
                        avg_annual_var = var_pct / nb_ans if nb_ans > 0 else 0
                        
                        # Interpretation with coloured dot
                        abs_avg_annual = abs(avg_annual_var)
                        if var_pct > 0:
                            if abs_avg_annual >= 20:
                                dot = "🔴"
                                interpretation = "Very high increase"
                            elif abs_avg_annual >= 10:
                                dot = "🟠"
                                interpretation = "High increase"
                            elif abs_avg_annual >= 3:
                                dot = "🟡"
                                interpretation = "Moderate increase"
                            else:
                                dot = "⚪"
                                interpretation = "Increase within norm"
                        elif var_pct < 0:
                            if abs_avg_annual >= 20:
                                dot = "🔵"
                                interpretation = "Very high decrease"
                            elif abs_avg_annual >= 10:
                                dot = "🟢"
                                interpretation = "High decrease"
                            elif abs_avg_annual >= 3:
                                dot = "🟢"
                                interpretation = "Moderate decrease"
                            else:
                                dot = "⚪"
                                interpretation = "Decrease within norm"
                        else:
                            dot = "⚪"
                            interpretation = "Stable"
                        
                        full_interpretation = f"{dot} {interpretation}"
                        
                        decomposition_data.append({
                            'Common Name': common,
                            'Total variation': f"{var_pct:+.1f}%",
                            'Annual variation': f"{avg_annual_var:+.1f}%/year",
                            'Rate effect': f"{rate_effect_pct:+.1f}%",
                            'Hours effect': f"{hours_effect_pct:+.1f}%",
                            'Other effect': f"{other_effect_pct:+.1f}%",
                            'Interpretation': full_interpretation,
                            # Raw data for chart
                            'var_pct': var_pct,
                            'taux_pct': rate_effect_pct,
                            'heures_pct': hours_effect_pct,
                            'autres_pct': other_effect_pct,
                            'var_annuelle': avg_annual_var
                        })
                
                if decomposition_data:
                    st.session_state.decomposition_data = decomposition_data
                    
                    # Table
                    df_display = pd.DataFrame(decomposition_data)[
                        ['Common Name', 'Total variation', 'Annual variation', 'Rate effect', 'Hours effect', 'Other effect', 'Interpretation']
                    ]
                    st.dataframe(df_display, use_container_width=True, hide_index=True)
                    
                    # Stacked bar chart
                    df_plot = pd.DataFrame(decomposition_data)
                    
                    fig = go.Figure()
                    
                    fig.add_trace(go.Bar(
                        name='Rate effect',
                        x=df_plot['Common Name'],
                        y=df_plot['taux_pct'],
                        marker_color='lightblue',
                        text=[f"{v:+.1f}%" for v in df_plot['taux_pct']],
                        textposition='inside'
                    ))
                    
                    fig.add_trace(go.Bar(
                        name='Hours effect',
                        x=df_plot['Common Name'],
                        y=df_plot['heures_pct'],
                        marker_color='lightcoral',
                        text=[f"{v:+.1f}%" for v in df_plot['heures_pct']],
                        textposition='inside'
                    ))
                    
                    fig.add_trace(go.Bar(
                        name='Other effect',
                        x=df_plot['Common Name'],
                        y=df_plot['autres_pct'],
                        marker_color='lightgreen',
                        text=[f"{v:+.1f}%" for v in df_plot['autres_pct']],
                        textposition='inside'
                    ))
                    
                    # Marker for total variation
                    fig.add_trace(go.Scatter(
                        name='Total variation',
                        x=df_plot['Common Name'],
                        y=df_plot['var_pct'],
                        mode='markers+lines',
                        marker=dict(size=10, color='black', symbol='diamond'),
                        line=dict(dash='dot', color='black'),
                        text=[f"Total: {v:+.1f}%" for v in df_plot['var_pct']],
                        textposition='top center'
                    ))
                    
                    fig.update_layout(
                        barmode='relative',
                        title=f"Decomposition of cost variation (as % of {cost_type.lower()} initial cost)",
                        yaxis_title="% of initial cost",
                        xaxis_title="Work Package",
                        hovermode='x unified',
                        height=500
                    )
                    
                    fig.add_hline(y=0, line_dash="solid", line_color="black", opacity=0.3)
                    
                    st.plotly_chart(fig, use_container_width=True, key="compet_chart")
                    
                    with st.expander("📘 How to read this chart?"):
                        st.markdown("""
                        - **Stacked bars**: contribution of each factor (rate, hours, other costs) to the total variation, expressed as a percentage of the initial cost of the work package.
                        - **Black diamond**: actual total variation (sum of the three effects).
                        - **Rate effect**: variation due to change in hourly rate (holding hours constant).
                        - **Hours effect**: variation due to change in number of hours (holding final rate constant).
                        - **Other effect**: variation of non‑labour costs (subcontracting, purchases, etc.).
                        - The **average annual variation** helps to gauge the drift relative to inflation (2‑3% per year). The coloured dots give a quick visual clue:
                            - 🔴 Very high increase (>20%/year)
                            - 🟠 High increase (10‑20%/year)
                            - 🟡 Moderate increase (3‑10%/year)
                            - 🟢 Decrease (>3%/year)
                            - 🔵 Very high decrease (>20%/year)
                            - ⚪ Within norm (<3%/year)
                        """)
                else:
                    st.info("Not enough data for decomposition (work packages without hours/rates variation or other costs).")
            else:
                st.info("Hourly rate data not available.")
        else:
            st.info("Hourly rate data not available.")

# --- ONGLET 7 : IA Analysis ---
with tabs[7]:
    if 'df_common' not in st.session_state:
        st.warning("Please apply the configuration first.")
    else:
        cost_type = st.radio("Cost type for analysis", ["Raw", "Normalized"], horizontal=True, key="cost_type_ia")
        st.session_state.cost_type = cost_type
        
        df_common = st.session_state.df_common
        chrono_order = st.session_state.chrono_order
        wp_drift_dict = st.session_state.get('wp_drift_dict', {})
        decomposition_data = st.session_state.get('decomposition_data', [])

        # Hot Topics for IA
        hot_points = []
        hot_points.append("🤖 AI analysis of observed drifts.")
        if decomposition_data:
            hot_points.append(f"📊 {len(decomposition_data)} work packages with decomposition.")
        if wp_drift_dict:
            hot_points.append(f"📈 {len([v for v in wp_drift_dict.values() if v['pente'] is not None])} work packages with computed drift.")
        display_hot_topic("Hot Topics - IA Analysis", hot_points)

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

            col_cost = 'Cout_Total' if cost_type == 'Raw' else 'Normalized_Cost'
            total = df_common.groupby('System')[col_cost].sum().reset_index()
            total = total.merge(df_common[['System','Date']].drop_duplicates(), on='System').sort_values('Date')

            if len(total) >= 2:
                x = (total['Date'] - total['Date'].min()).dt.days.values
                y = total[col_cost].values
                try:
                    coeffs = np.polyfit(x, y, 1)
                    slope_day = coeffs[0]
                    first_cost = y[0]
                    annual_pct = (slope_day * 365 / first_cost) * 100 if first_cost else 0
                    global_trend = f"global slope = {slope_day:.2f} k€/day, i.e. {annual_pct:.1f}% per year (initial total cost = {first_cost:.2f} k€)"
                except np.linalg.LinAlgError:
                    global_trend = "Global trend not available (SVD error)"
            else:
                global_trend = "Global trend not available"

            wp_trends = []
            for common, vals in wp_drift_dict.items():
                if vals['pente'] is not None:
                    wp_trends.append(f"- {common}: annualized drift = {vals['annual']:.1f}%")
            wp_trends_str = "\n".join(wp_trends) if wp_trends else "No per-WP trends"

            decomp_summary = ""
            if decomposition_data:
                decomp_summary = "\n\n**Rate vs Hours decomposition (based on first and last bid):**\n"
                for d in decomposition_data:
                    interp_clean = d['Interpretation'].split(' ', 1)[-1] if ' ' in d['Interpretation'] else d['Interpretation']
                    decomp_summary += f"- {d['Common Name']}: Total Δ = {d['Total variation']} ({d['Annual variation']}), Rate effect = {d['Rate effect']}, Hours effect = {d['Hours effect']}, Other effect = {d['Other effect']} → {interp_clean}\n"

            prompt = f"""
You are an expert in aerospace project analysis. We have calculated {cost_type.lower()} cost drifts for three versions (Alpha, Beta, Gamma) with their actual dates. All costs are in k€ (thousands of euros) unless specified otherwise. Work packages are grouped by Common Name.

**Global trend**:
{global_trend}

**Per Work Package trends**:
{wp_trends_str}
{decomp_summary}

Questions:
1. Which Work Packages show the strongest upward drift? Which are stable or decreasing?
2. Is the global drift concerning compared to normal inflation (2-3% per year)?
3. Based on the decomposition, which WPs are losing technical competitiveness (hours effect positive and significant)?
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
                pivot = st.session_state.pivot_norm_common if cost_type == 'Normalized' else st.session_state.pivot_raw_common
                summary = f"""
ACTUAL PROJECT DATA (all costs in k€ unless otherwise indicated):
- Chronological order: {' -> '.join(chrono_order)}
- Dates: { {k: v.strftime('%Y-%m-%d') for k,v in dates.items()} }
- Analysis level: {st.session_state.level_choice}
- {cost_type} costs per Common Name:
{pivot.to_string()}
"""
                prompt_full = f"""
Act as a Senior Airbus Project Controller. Analyze the cost drift based on the real data below. All costs are in k€ (thousands of euros) unless specified otherwise. Work packages are grouped by Common Name.

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

# --- ONGLET 8 : Validation ---
with tabs[8]:
    st.subheader("Validation")
    st.sidebar.header("🧪 Validation Mode")
    oracle_file = st.sidebar.file_uploader("Load oracle file", type=["xlsx", "csv"], key="oracle_upload")
    if oracle_file:
        st.info("Oracle loaded -- comparison would appear here.")
    else:
        st.info("Load an oracle file to validate results.")

# --- GUIDE ---
st.divider()
st.subheader("📚 Audit Guide")
st.markdown("""
* **Configuration**: Set WBS level and define common names for work packages (above the tabs). Click 'Apply Configuration' to save. After saving, you can see the resulting groups in the expander.
* **WBS Structure**: Visual tree of the Work Breakdown Structure across bids (no costs, pure hierarchy).
* **Input Data**: Aggregated data tables by Common Name. An expander shows the detailed view by original code.
* **Global Analysis**: Overall cost trends, stacked cost by type, and breakdown by WBS level.
* **WP analysis**: Cost views per Common Name. Select Common Names via checkboxes.
* **Bridges**: Visual decomposition of cost differences between any two bids, grouped by Common Name, with heatmap of annual variations.
* **Drift analysis**: Global trend and per-Common Name annualized drift.
* **competitiveness deep dive**: Decomposition of cost variations into rate and hours effects, by Common Name.
* **IA Analysis**: AI commentary on drifts.
* **Validation**: Compare against an oracle file.

**Note**: All costs are displayed in k€ (thousands of euros) or M€ (millions of euros) depending on the magnitude. The numbers on the axes represent the chosen unit. Hours are in hours, rates in €/h.
""")