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
    """Convertit une valeur en chaîne, la nettoie et retourne NaN si vide ou 'nan'."""
    if pd.isna(val):
        return np.nan
    s = str(val).strip()
    if s == '' or s.lower() in ['nan', 'none', 'null']:
        return np.nan
    return s

def parse_complex_devis(df, system_name):
    data = df.iloc[3:].copy()
    # Nettoyer les colonnes WBS
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

# ========== CONSTRUCTION DE LA HIÉRARCHIE WBS (sans coûts) ==========
def build_wbs_hierarchy(raw_dfs, system=None):
    """
    Construit une DataFrame hiérarchique à partir des colonnes WBS.
    Ne contient que la structure, pas les coûts.
    """
    hierarchy_rows = []
    
    for sys_name, df in raw_dfs.items():
        if system and sys_name != system:
            continue
            
        df_sys = df.copy()
        # Ne garder que les lignes avec au moins un code WBS
        df_sys = df_sys.dropna(subset=['WBS_2', 'WBS_3', 'WBS_4', 'WBS_5'], how='all')
        
        # Ensemble pour éviter les doublons dans la structure
        seen_nodes = set()
        
        for _, row in df_sys.iterrows():
            wbs_2 = row['WBS_2']
            wbs_3 = row['WBS_3']
            wbs_4 = row['WBS_4']
            wbs_5 = row['WBS_5']
            
            # Niveau 1
            node_id = f"{sys_name}_L1_{wbs_2}"
            if node_id not in seen_nodes and pd.notna(wbs_2):
                seen_nodes.add(node_id)
                hierarchy_rows.append({
                    'id': node_id,
                    'parent': '',
                    'name': str(wbs_2),
                    'level': 1,
                    'system': sys_name,
                    'path': str(wbs_2)
                })
            
            # Niveau 2 (si existe)
            if pd.notna(wbs_3):
                node_id = f"{sys_name}_L2_{wbs_2}_{wbs_3}"
                if node_id not in seen_nodes:
                    seen_nodes.add(node_id)
                    hierarchy_rows.append({
                        'id': node_id,
                        'parent': f"{sys_name}_L1_{wbs_2}",
                        'name': str(wbs_3),
                        'level': 2,
                        'system': sys_name,
                        'path': f"{wbs_2} / {wbs_3}"
                    })
            
            # Niveau 3 (si existe)
            if pd.notna(wbs_4):
                node_id = f"{sys_name}_L3_{wbs_2}_{wbs_3}_{wbs_4}"
                if node_id not in seen_nodes:
                    seen_nodes.add(node_id)
                    hierarchy_rows.append({
                        'id': node_id,
                        'parent': f"{sys_name}_L2_{wbs_2}_{wbs_3}",
                        'name': str(wbs_4),
                        'level': 3,
                        'system': sys_name,
                        'path': f"{wbs_2} / {wbs_3} / {wbs_4}"
                    })
            
            # Niveau 4 (si existe)
            if pd.notna(wbs_5):
                node_id = f"{sys_name}_L4_{wbs_2}_{wbs_3}_{wbs_4}_{wbs_5}"
                if node_id not in seen_nodes:
                    seen_nodes.add(node_id)
                    hierarchy_rows.append({
                        'id': node_id,
                        'parent': f"{sys_name}_L3_{wbs_2}_{wbs_3}_{wbs_4}",
                        'name': str(wbs_5),
                        'level': 4,
                        'system': sys_name,
                        'path': f"{wbs_2} / {wbs_3} / {wbs_4} / {wbs_5}"
                    })
    
    df_hierarchy = pd.DataFrame(hierarchy_rows)
    return df_hierarchy

# ========== WBS LEVEL SELECTION (HORS ONGLETS) ==========
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

# ========== GESTION DU MAPPING (HORS ONGLETS) ==========
st.subheader("Technical Normalization Matrix")

# Construction de la liste des WP pour le niveau choisi, en ignorant les NaN
wp_list = []
for name, df in raw_dfs.items():
    codes = df[level_col].dropna().unique()  # Ne garder que les codes non vides
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

# ========== AGRÉGATION DES DONNÉES (UTILISÉ DANS TOUS LES ONGLETS) ==========
all_rows = []
for name, df in raw_dfs.items():
    df['Date'] = pd.to_datetime(dates[name])
    # Filtrer pour ne garder que les lignes où la colonne du niveau sélectionné n'est pas vide
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

# Stocker df_global dans session_state pour les autres onglets
st.session_state.df_global = df_global
st.session_state.level_choice = level_choice
st.session_state.pivot_raw_code = df_global.pivot_table(index='Code', columns='System', values='Cout_Total', aggfunc='sum').fillna(0)
st.session_state.pivot_norm_code = df_global.pivot_table(index='Code', columns='System', values='Normalized_Cost', aggfunc='sum').fillna(0)
st.session_state.code_to_unique = df_global.groupby('Code')['Unique_Label'].first().to_dict()
st.session_state.chrono_order = df_global[['System', 'Date']].drop_duplicates().sort_values('Date')['System'].tolist()
st.session_state.wp_drift_dict = None
st.session_state.decomposition_data = None

# ========== FONCTIONS UTILITAIRES POUR LE FORMATAGE DES COÛTS ==========
def format_cost_value(value, decimals=1):
    """Formate une valeur en k€ en une chaîne avec l'unité appropriée (k€ ou M€)."""
    if pd.isna(value):
        return ""
    if abs(value) >= 1000:
        return f"{value/1000:.{decimals}f} M€"
    else:
        return f"{value:.{decimals}f} k€"

def format_cost_series(series):
    """Applique format_cost_value à une série."""
    return series.apply(lambda x: format_cost_value(x, 1))

def adjust_fig_axis_for_cost(fig, data_values, axis_title="Cost"):
    """Ajuste le titre de l'axe Y et les données si nécessaire."""
    max_val = data_values.max() if hasattr(data_values, 'max') else max(data_values)
    if max_val >= 1000:
        fig.update_traces(y=data_values/1000)
        fig.update_layout(yaxis_title=f"{axis_title} (M€)")
    else:
        fig.update_layout(yaxis_title=f"{axis_title} (k€)")
    fig.update_yaxes(tickformat=".1f")
    return fig

# ========== ORGANISATION PAR ONGLETS (9 onglets) ==========
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
    st.divider()
    st.subheader("🌳 Work Breakdown Structure (WBS) - Hierarchical View")
    st.caption("This shows the structure of work packages across all bids. No costs are displayed, only the hierarchy.")
    
    if not raw_dfs:
        st.warning("Please upload or place the three Devis files first.")
    else:
        # Sélecteur de système
        selected_system = st.selectbox(
            "Select system to display (or 'All')",
            options=["All"] + files_list,
            index=0,
            key="wbs_structure_system"
        )
        
        # Construire la hiérarchie
        with st.spinner("Building WBS structure..."):
            system_param = None if selected_system == "All" else selected_system
            df_hierarchy = build_wbs_hierarchy(raw_dfs, system_param)
        
        if df_hierarchy.empty:
            st.warning("No hierarchical structure found.")
        else:
            # Créer un arbre interactif avec treemap (sans valeurs)
            # On utilise des valeurs constantes pour que tous les rectangles aient la même taille
            df_hierarchy['dummy_value'] = 1
            
            fig = px.treemap(
                df_hierarchy,
                ids='id',
                parents='parent',
                names='name',
                values='dummy_value',
                title=f"WBS Structure - {selected_system if selected_system != 'All' else 'All bids'}",
                color='level',
                color_continuous_scale='Blues',
                hover_data={'path': True, 'level': True, 'system': True}
            )
            fig.update_traces(
                textinfo="label",
                hovertemplate='<b>%{label}</b><br>Level: %{customdata[1]}<br>System: %{customdata[2]}<br>Path: %{customdata[0]}<extra></extra>',
                customdata=df_hierarchy[['path', 'level', 'system']].values
            )
            fig.update_layout(height=700, coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
            
            st.info("""
            💡 **How to read**: This treemap shows the hierarchical breakdown of work packages.
            - Each rectangle represents a work package.
            - Size is uniform (all packages same size) – only structure matters.
            - Color indicates level (darker = deeper level).
            - Click on a rectangle to zoom into its children.
            """)
            
            # Afficher la structure sous forme de tableau
            with st.expander("📋 Show hierarchical table"):
                # Ajouter des colonnes pour faciliter la lecture
                df_display = df_hierarchy.copy()
                # Créer une colonne d'indentation pour visualiser la hiérarchie
                df_display['indent'] = df_display['level'].apply(lambda x: '  ' * (x-1) + '└─ ')
                df_display['display_name'] = df_display.apply(lambda row: f"{row['indent']}{row['name']} (L{row['level']})", axis=1)
                df_display = df_display[['system', 'level', 'path', 'display_name']].sort_values(['system', 'path'])
                st.dataframe(df_display, use_container_width=True, hide_index=True)

# --- ONGLET 1 : Input Data (tableaux récapitulatifs avec totaux et ordre chronologique) ---
with tabs[1]:
    st.divider()
    st.subheader(f"Aggregated Data per {level_choice}")

    # Déterminer l'ordre chronologique des systèmes
    chrono_systems = sorted(dates.keys(), key=lambda x: dates[x])
    # Créer un mapping pour les nouveaux noms de colonnes avec date
    system_display_names = {sys: f"{sys} ({dates[sys].strftime('%Y-%m-%d')})" for sys in chrono_systems}

    def create_summary_table(value_col, unit_label):
        pivot = df_global.pivot_table(index='Code', columns='System', values=value_col, aggfunc='sum').fillna(0)
        # Ajouter le nom commun
        names = df_global.groupby('Code')['Common_Name'].first()
        pivot = pivot.join(names)
        # Réinitialiser l'index pour que 'Code' devienne une colonne
        pivot = pivot.reset_index()
        # Renommer les colonnes des systèmes avec les dates
        pivot.rename(columns=system_display_names, inplace=True)
        # Réorganiser les colonnes : Code, Common_Name, puis les systèmes dans l'ordre chronologique
        system_cols = [system_display_names[sys] for sys in chrono_systems]
        cols = ['Code', 'Common_Name'] + system_cols
        pivot = pivot[cols]
        return pivot, system_cols

    def add_total_row(pivot, num_cols):
        """Ajoute une ligne 'TOTAL' avec la somme de chaque colonne numérique."""
        pivot_with_total = pivot.copy()
        total_values = {col: pivot_with_total[col].sum() for col in num_cols}
        total_values['Code'] = 'TOTAL'
        total_values['Common_Name'] = 'TOTAL'
        total_df = pd.DataFrame([total_values])
        pivot_with_total = pd.concat([pivot_with_total, total_df], ignore_index=True)
        return pivot_with_total

    def format_cost_dataframe(pivot, num_cols):
        """Retourne un DataFrame avec les colonnes numériques formatées en texte (k€ ou M€)."""
        df_display = pivot.copy()
        for col in num_cols:
            df_display[col] = df_display[col].apply(lambda x: format_cost_value(x, 1))
        return df_display

    st.write("**Raw Costs**")
    raw_table, num_cols_raw = create_summary_table('Cout_Total', "k€")
    raw_table_with_total = add_total_row(raw_table, num_cols_raw)
    raw_table_display = format_cost_dataframe(raw_table_with_total, num_cols_raw)
    st.dataframe(raw_table_display, use_container_width=True, hide_index=True)

    st.write("**Normalized Costs**")
    norm_table, num_cols_norm = create_summary_table('Normalized_Cost', "k€")
    norm_table_with_total = add_total_row(norm_table, num_cols_norm)
    norm_table_display = format_cost_dataframe(norm_table_with_total, num_cols_norm)
    st.dataframe(norm_table_display, use_container_width=True, hide_index=True)

    if df_global['Heures'].sum() > 0:
        st.write("**Hours**")
        hours_table, num_cols_hours = create_summary_table('Heures', "h")
        hours_table_with_total = add_total_row(hours_table, num_cols_hours)
        hours_display = hours_table_with_total.copy()
        for col in num_cols_hours:
            hours_display[col] = hours_display[col].apply(lambda x: f"{x:.0f} h")
        st.dataframe(hours_display, use_container_width=True, hide_index=True)

    if df_global['Taux_Horaire'].notna().any():
        st.write("**Average Hourly Rates**")
        rates_table, num_cols_rates = create_summary_table('Taux_Horaire', "€/h")
        # Pas de total pour les taux
        rates_display = rates_table.copy()
        for col in num_cols_rates:
            rates_display[col] = rates_display[col].apply(lambda x: f"{x:.2f} €/h")
        st.dataframe(rates_display, use_container_width=True, hide_index=True)

# --- ONGLET 2 : Global Analysis (graphiques globaux) ---
with tabs[2]:
    st.divider()

    # Déterminer l'ordre chronologique des systèmes à partir des dates
    chrono_systems = sorted(dates.keys(), key=lambda x: dates[x])

    # Calculer les totaux par niveau sélectionné et par système, en ignorant les NaN
    overall_view_data = []
    for name, df in raw_dfs.items():
        df_sys = df.copy()
        df_sys['System'] = name
        # Filtrer les lignes où la colonne du niveau n'est pas vide
        df_filtered = df_sys[df_sys[level_col].notna()].copy()
        if df_filtered.empty:
            continue
        grouped = df_filtered.groupby(level_col).agg({'Cout_Total': 'sum'}).reset_index()
        grouped['System'] = name
        overall_view_data.append(grouped)
    
    if overall_view_data:
        df_overall_view = pd.concat(overall_view_data, ignore_index=True)
    else:
        st.warning(f"No data for level {level_choice}")
        st.stop()

    # --- GRAPHIQUE LINÉAIRE (Total Global Cost per Bid) ---
    st.subheader("Total Global Cost per Bid (chronological order)")
    total_global = df_overall_view.groupby('System')['Cout_Total'].sum().reset_index()
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
    # Ajustement de l'axe
    max_val = total_global['Cout_Total'].max()
    if max_val >= 1000:
        fig_total.update_traces(y=total_global['Cout_Total']/1000)
        fig_total.update_layout(yaxis_title="Cost (M€)")
    else:
        fig_total.update_layout(yaxis_title="Cost (k€)")
    fig_total.update_yaxes(tickformat=".1f")
    st.plotly_chart(fig_total, use_container_width=True, key="global_line")

    st.divider()
    
    # --- GRAPHIQUE À BARRES (par niveau sélectionné) ---
    st.subheader(f"Global View - {level_choice} (Sum of all Work Packages)")
    fig_global = px.bar(
        df_overall_view,
        x=level_col,
        y='Cout_Total',
        color='System',
        barmode='group',
        title=f"Total Raw Costs by {level_choice} (all sub-WPs included)",
        category_orders={"System": chrono_systems},
        labels={"Cout_Total": "Cost", level_col: "WBS Code"}
    )
    fig_global.update_xaxes(tickangle=45)
    # Ajustement de l'axe
    max_val_bar = df_overall_view['Cout_Total'].max()
    if max_val_bar >= 1000:
        fig_global.update_traces(y=df_overall_view['Cout_Total']/1000)
        fig_global.update_layout(yaxis_title="Cost (M€)")
    else:
        fig_global.update_layout(yaxis_title="Cost (k€)")
    fig_global.update_yaxes(tickformat=".1f")
    st.plotly_chart(fig_global, use_container_width=True, key="global_bar")
    st.caption(f"This chart shows the sum of all underlying Work Packages for each {level_choice} code, across the three bids.")

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
    
    # Ajustement de l'unité
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
    fig.update_layout(
        title=f"{title_prefix}Bridge: {base_sys} → {target_sys}",
        yaxis_title=f"Cost ({unit})"
    )
    fig.update_yaxes(tickformat=".1f")
    return fig

# --- ONGLET 3 : WP analysis (analyse détaillée par work package) ---
with tabs[3]:
    st.divider()

    if 'df_global' not in st.session_state:
        st.warning("Please configure the level and mapping in the first tab first.")
    else:
        df_global = st.session_state.df_global
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
            # Bar chart raw
            fig_raw_bar = px.bar(df_filtered, x="Unique_Label", y="Cout_Total", color="System", 
                                 barmode="group", title="Raw Volume",
                                 labels={"Cout_Total": "Cost", "Unique_Label": "Work Package"})
            fig_raw_bar.update_xaxes(tickangle=45)
            max_val_raw = df_filtered['Cout_Total'].max()
            if max_val_raw >= 1000:
                fig_raw_bar.update_traces(y=df_filtered['Cout_Total']/1000)
                fig_raw_bar.update_layout(yaxis_title="Cost (M€)")
            else:
                fig_raw_bar.update_layout(yaxis_title="Cost (k€)")
            fig_raw_bar.update_yaxes(tickformat=".1f")
            st.plotly_chart(fig_raw_bar, use_container_width=True, key="raw_vol")

            # Line chart raw
            fig_raw_line = px.line(df_filtered, x="Date", y="Cout_Total", color="Unique_Label", 
                                   markers=True, title="Raw Timeline",
                                   labels={"Cout_Total": "Cost", "Date": "Date"})
            if max_val_raw >= 1000:
                fig_raw_line.update_traces(y=df_filtered['Cout_Total']/1000)
                fig_raw_line.update_layout(yaxis_title="Cost (M€)")
            else:
                fig_raw_line.update_layout(yaxis_title="Cost (k€)")
            fig_raw_line.update_yaxes(tickformat=".1f")
            st.plotly_chart(fig_raw_line, use_container_width=True, key="raw_time")
        else:
            st.info("No data for selected Work Packages.")

        st.subheader("Normalized Data")
        if not df_filtered.empty:
            # Bar chart normalized
            fig_norm_bar = px.bar(df_filtered, x="Unique_Label", y="Normalized_Cost", color="System", 
                                  barmode="group", title="Normalized Volume",
                                  labels={"Normalized_Cost": "Normalized Cost", "Unique_Label": "Work Package"})
            fig_norm_bar.update_xaxes(tickangle=45)
            max_val_norm = df_filtered['Normalized_Cost'].max()
            if max_val_norm >= 1000:
                fig_norm_bar.update_traces(y=df_filtered['Normalized_Cost']/1000)
                fig_norm_bar.update_layout(yaxis_title="Normalized Cost (M€)")
            else:
                fig_norm_bar.update_layout(yaxis_title="Normalized Cost (k€)")
            fig_norm_bar.update_yaxes(tickformat=".1f")
            st.plotly_chart(fig_norm_bar, use_container_width=True, key="norm_vol")

            # Line chart normalized
            fig_norm_line = px.line(df_filtered, x="Date", y="Normalized_Cost", color="Unique_Label", 
                                    markers=True, title="Normalized Timeline",
                                    labels={"Normalized_Cost": "Normalized Cost", "Date": "Date"})
            if max_val_norm >= 1000:
                fig_norm_line.update_traces(y=df_filtered['Normalized_Cost']/1000)
                fig_norm_line.update_layout(yaxis_title="Normalized Cost (M€)")
            else:
                fig_norm_line.update_layout(yaxis_title="Normalized Cost (k€)")
            fig_norm_line.update_yaxes(tickformat=".1f")
            st.plotly_chart(fig_norm_line, use_container_width=True, key="norm_time")
        else:
            st.info("No data for selected Work Packages.")

# --- ONGLET 4 : Bridges ---
with tabs[4]:
    st.divider()

    if 'df_global' not in st.session_state:
        st.warning("Please configure the level and mapping in the first tab first.")
    else:
        pivot_raw_code = st.session_state.pivot_raw_code
        pivot_norm_code = st.session_state.pivot_norm_code
        code_to_unique = st.session_state.code_to_unique
        files_list = ["Devis_Alpha", "Devis_Beta", "Devis_Gamma"]

        st.subheader("Bridge Charts - Cost Decomposition Between Bids")
        st.markdown("""
        Bridge charts show the step-by-step contribution of each Work Package to the total cost difference between two bids.
        Positive values (above zero) increase the total cost, negative values decrease it.
        """)

        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Raw Cost Bridge**")
            pivot_raw_unique = pivot_raw_code.rename(index=code_to_unique)
            base_r = st.selectbox("Base bid (Raw)", files_list, index=0, key="base_raw")
            target_options = [s for s in files_list if s != base_r]
            target_r = st.selectbox("Target bid (Raw)", target_options, index=0, key="target_raw")
            if base_r != target_r:
                st.plotly_chart(draw_bridge(pivot_raw_unique, base_r, target_r, "Raw "), use_container_width=True, key="raw_bridge")
            else:
                st.warning("Choose two different systems")

        with col2:
            st.markdown("**Normalized Cost Bridge**")
            pivot_norm_unique = pivot_norm_code.rename(index=code_to_unique)
            base_n = st.selectbox("Base bid (Normalized)", files_list, index=0, key="base_norm")
            target_options_n = [s for s in files_list if s != base_n]
            target_n = st.selectbox("Target bid (Normalized)", target_options_n, index=0, key="target_norm")
            if base_n != target_n:
                st.plotly_chart(draw_bridge(pivot_norm_unique, base_n, target_n, "Normalized "), use_container_width=True, key="norm_bridge")
            else:
                st.warning("Choose two different systems")

        st.info("💡 **Interpretation**: The chart starts with the total cost of the base bid. Each bar shows how much a specific Work Package adds or subtracts to reach the target bid total.")

# --- ONGLET 5 : Drift analysis ---
with tabs[5]:
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

            # Ajustement de l'unité pour le graphique
            max_val = total_norm['Normalized_Cost'].max()
            unit = "M€" if max_val >= 1000 else "k€"
            factor = 1000 if unit == "M€" else 1
            y_adjusted = total_norm['Normalized_Cost'] / factor

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=total_norm['Date'], y=y_adjusted, 
                                     mode='markers+lines', name='Total',
                                     text=[f"{v:.1f}" for v in y_adjusted]))
            # Tendance linéaire
            trend_y = np.polyval([slope_day/factor, coeffs[1]/factor if 'coeffs' in locals() else 0], [0, x.max()])
            fig.add_trace(go.Scatter(x=[total_norm['Date'].min(), total_norm['Date'].max()], 
                                     y=trend_y, 
                                     mode='lines', line=dict(dash='dash', color='red'), name='Trend'))
            fig.update_layout(title="Total Normalized Cost Over Time", 
                             xaxis_title="Date", yaxis_title=f"Cost ({unit})")
            fig.update_yaxes(tickformat=".1f")
            st.plotly_chart(fig, use_container_width=True, key="global_drift")
            col1, col2, col3 = st.columns(3)
            col1.metric("Daily drift", f"{slope_day:+.2f} k€/day")
            col2.metric("Monthly drift", f"{slope_month:+.2f} k€/month")
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
                    # Ajustement unité pour ce WP
                    max_val_wp = sub['Normalized_Cost'].max()
                    unit_wp = "M€" if max_val_wp >= 1000 else "k€"
                    factor_wp = 1000 if unit_wp == "M€" else 1
                    y_wp = sub['Normalized_Cost'] / factor_wp
                    fig = px.line(sub, x='Date', y=y_wp, text='System', markers=True, 
                                 title=f"{display_name} normalized cost",
                                 labels={"y": f"Cost ({unit_wp})", "Date": "Date"})
                    fig.update_traces(textposition='top center')
                    fig.update_yaxes(tickformat=".1f")
                    st.plotly_chart(fig, use_container_width=True, key="wp_detail")
                else:
                    st.warning("No data for this Work Package.")

# --- ONGLET 6 : competitiveness deep dive ---
with tabs[6]:
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
                    fig.add_trace(go.Bar(name='Rate share', x=df_plot['WP'], 
                                        y=[float(s.replace('%','').replace('+','')) for s in df_plot['Rate share']], 
                                        marker_color='lightblue', text=[s for s in df_plot['Rate share']]))
                    fig.add_trace(go.Bar(name='Hours share', x=df_plot['WP'], 
                                        y=[float(s.replace('%','').replace('+','')) for s in df_plot['Hours share']], 
                                        marker_color='lightcoral', text=[s for s in df_plot['Hours share']]))
                    fig.add_trace(go.Bar(name='Cross share', x=df_plot['WP'], 
                                        y=[float(s.replace('%','').replace('+','')) for s in df_plot['Cross share']], 
                                        marker_color='lightgreen', text=[s for s in df_plot['Cross share']]))
                    fig.update_layout(barmode='stack', 
                                     title="Contribution shares to total cost variation",
                                     yaxis_title="% of total variation",
                                     xaxis_title="Work Package")
                    fig.update_traces(textposition='inside')
                    st.plotly_chart(fig, use_container_width=True, key="compet_chart")
                else:
                    st.info("Not enough data for decomposition (no WP with varying hours/rates).")
            else:
                st.info("Hourly rate data not available for any WP.")
        else:
            st.info("Hourly rate data not available.")

# --- ONGLET 7 : IA Analysis ---
with tabs[7]:
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
                    global_trend = f"global slope = {slope_day:.2f} k€/day, i.e. {annual_pct:.1f}% per year (initial total cost = {first_cost:.2f} k€)"
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
You are an expert in aerospace project analysis. We have calculated normalized cost drifts for three versions (Alpha, Beta, Gamma) with their actual dates. All costs are in k€ (thousands of euros) unless specified otherwise.

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
ACTUAL PROJECT DATA (all costs in k€ unless otherwise indicated):
- Chronological order: {' -> '.join(chrono_order)}
- Dates: { {k: v.strftime('%Y-%m-%d') for k,v in dates.items()} }
- Analysis level: {st.session_state.level_choice}
- Normalized costs per WP:
{pivot_norm_unique.to_string()}
"""
                prompt_full = f"""
Act as a Senior Airbus Project Controller. Analyze the cost drift based on the real data below. All costs are in k€ (thousands of euros) unless specified otherwise.
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
    st.divider()
    st.sidebar.header("🧪 Validation Mode")
    oracle_file = st.sidebar.file_uploader("Load oracle file", type=["xlsx", "csv"], key="oracle_upload")
    if oracle_file:
        st.info("Oracle loaded – comparison would appear here.")
    else:
        st.info("Load an oracle file to validate results.")

# --- GUIDE ---
st.divider()
st.subheader("📚 Audit Guide")
st.markdown("""
* **WBS Structure**: Visual tree of the Work Breakdown Structure across bids (no costs, pure hierarchy).
* **Input Data**: Aggregated data tables at the selected WBS level. The mapping matrix above affects all tabs.
* **Global Analysis**: Overall cost trends and breakdown by WBS level.
* **WP analysis**: Raw and normalized cost views per Work Package. Select WPs via checkboxes (Select All/Clear All).
* **Bridges**: Visual decomposition of cost differences between any two bids.
* **Drift analysis**: Global trend and per-WP annualized drift (calculated across all systems).
* **competitiveness deep dive**: Decomposition of cost variations into rate (inflation) and hours (technical) effects.
* **IA Analysis**: AI commentary on drifts.
* **Validation**: Compare against an oracle file.

**Note**: All costs are displayed in k€ (thousands of euros) or M€ (millions of euros) depending on the magnitude. The numbers on the axes represent the chosen unit. Hours are in hours, rates in €/h.
""")