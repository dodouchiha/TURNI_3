import streamlit as st
import pandas as pd
import calendar
from datetime import datetime
import holidays
from io import BytesIO
import requests
import json
import base64

# --- CONFIGURAZIONE INIZIALE ---
st.set_page_config(page_title="Gestione Turni Medici", layout="wide")

# --- COSTANTI ---
COL_DATA = "Data"
COL_GIORNO = "Giorno"
COL_FESTIVO = "Festivo" # Necessaria per la logica di styling
COL_NOME_FESTIVO = "Nome Festivo" # Mostrata all'utente
TIPI_ASSENZA = ["Presente", "Ferie", "Malattia", "Congresso", "Lezione", "Altro"]

# --- CONFIGURAZIONE GITHUB ---
GITHUB_USER = "dodouchiha"
REPO_NAME = "turni_3"
FILE_PATH = "medici.json"
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN")

if not GITHUB_TOKEN:
    st.error("Token GitHub non configurato nelle secrets! L'applicazione non potrà salvare i dati.")
    st.stop()

API_URL = f"https://api.github.com/repos/{GITHUB_USER}/{REPO_NAME}/contents/{FILE_PATH}"
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

# --- FUNZIONI GITHUB ---
def carica_medici():
    try:
        res = requests.get(API_URL, headers=HEADERS)
        res.raise_for_status()
        contenuto = res.json()
        file_sha = contenuto["sha"]
        elenco_json = base64.b64decode(contenuto["content"]).decode('utf-8')
        elenco = json.loads(elenco_json)
        st.session_state.sha_medici = file_sha
        return elenco
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            st.warning(f"File '{FILE_PATH}' non trovato su GitHub. Verrà creato al primo salvataggio.")
            st.session_state.sha_medici = None
            return []
        else:
            st.error(f"Errore GitHub (carica): {e.response.status_code} - {e.response.text}")
            return []
    except Exception as e:
        st.error(f"Errore imprevisto durante il caricamento dei medici: {e}")
        return []

def salva_medici(lista_medici):
    sha = st.session_state.get("sha_medici")
    blob = json.dumps(lista_medici, indent=2).encode('utf-8')
    encoded_content = base64.b64encode(blob).decode('utf-8')
    data = {
        "message": f"Aggiornamento elenco medici - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "content": encoded_content,
        "branch": "main"
    }
    if sha:
        data["sha"] = sha
    try:
        res = requests.put(API_URL, headers=HEADERS, json=data)
        res.raise_for_status()
        if res.status_code in [200, 201]:
            st.session_state.sha_medici = res.json()["content"]["sha"]
            return True
        return False
    except requests.exceptions.HTTPError as e:
        st.sidebar.error(f"Errore GitHub (salva): {e.response.status_code}")
        print(f"Dettagli errore GitHub: {e.response.text}")
        try: print(f"Corpo errore JSON: {e.response.json()}")
        except ValueError: pass
        return False
    except Exception as e:
        st.sidebar.error(f"Errore imprevisto durante il salvataggio dei medici: {e}")
        print(f"Errore imprevisto (salva_medici): {e}")
        return False

# --- INIZIALIZZAZIONE STATO ---
if 'elenco_medici_completo' not in st.session_state:
    st.session_state.elenco_medici_completo = carica_medici()
if 'sha_medici' not in st.session_state:
    st.session_state.sha_medici = None

# --- GESTIONE MEDICI - Sidebar ---
st.sidebar.header("👨‍⚕️ Gestione Medici")
with st.sidebar.form("form_aggiungi_medico", clear_on_submit=True):
    nuovo_medico = st.text_input("➕ Nome nuovo medico").strip()
    submitted_add = st.form_submit_button("Aggiungi Medico")

if submitted_add and nuovo_medico:
    if nuovo_medico not in st.session_state.elenco_medici_completo:
        elenco_aggiornato = st.session_state.elenco_medici_completo + [nuovo_medico]
        elenco_aggiornato.sort()
        if salva_medici(elenco_aggiornato):
            st.session_state.elenco_medici_completo = elenco_aggiornato
            st.toast(f"Medico '{nuovo_medico}' aggiunto!", icon="✅")
            st.rerun()
    else:
        st.sidebar.warning(f"'{nuovo_medico}' è già presente nell'elenco.")

if st.session_state.elenco_medici_completo:
    options_rimuovi = [""] + sorted(list(set(st.session_state.elenco_medici_completo)))
    current_selection_rimuovi = st.session_state.get("medico_da_rimuovere_selection", "")
    try:
        default_index_rimuovi = options_rimuovi.index(current_selection_rimuovi)
    except ValueError: default_index_rimuovi = 0
    medico_da_rimuovere = st.sidebar.selectbox(
        "🗑️ Rimuovi medico", options=options_rimuovi, index=default_index_rimuovi,
        key="selectbox_rimuovi_medico_key"
    )
    st.session_state.medico_da_rimuovere_selection = medico_da_rimuovere
    if st.sidebar.button("Conferma Rimozione", key="button_conferma_rimozione") and medico_da_rimuovere:
        medici_temp = st.session_state.elenco_medici_completo.copy()
        medici_temp.remove(medico_da_rimuovere)
        if salva_medici(medici_temp):
            st.session_state.elenco_medici_completo = medici_temp
            st.toast(f"Medico '{medico_da_rimuovere}' rimosso.", icon="🗑️")
            if 'medici_pianificati' in st.session_state and medico_da_rimuovere in st.session_state.medici_pianificati:
                st.session_state.medici_pianificati.remove(medico_da_rimuovere)
            if 'df_turni' in st.session_state: del st.session_state.df_turni
            st.session_state.medico_da_rimuovere_selection = ""
            st.rerun()
else:
    st.sidebar.caption("Nessun medico nell'elenco.")

st.sidebar.markdown("---")
default_medici_pianificati = st.session_state.get('medici_pianificati', [])
default_medici_pianificati = [m for m in default_medici_pianificati if m in st.session_state.elenco_medici_completo]
if not default_medici_pianificati and st.session_state.elenco_medici_completo:
    default_medici_pianificati = st.session_state.elenco_medici_completo.copy()
medici_pianificati = st.sidebar.multiselect(
    "✅ Seleziona medici da pianificare",
    options=sorted(list(set(st.session_state.elenco_medici_completo))),
    default=default_medici_pianificati, key="multiselect_medici_pianificati"
)
if 'medici_pianificati' not in st.session_state or \
   set(st.session_state.medici_pianificati) != set(medici_pianificati):
    st.session_state.medici_pianificati = medici_pianificati
    if 'df_turni' in st.session_state: del st.session_state.df_turni

# --- SELEZIONE MESE E ANNO ---
st.sidebar.markdown("---")
st.sidebar.header("🗓️ Selezione Periodo")
oggi = datetime.today()
idx_mese_default = st.session_state.get('selected_mese_index', oggi.month - 1)
idx_anno_default = st.session_state.get('selected_anno_index', 2)
col1_sidebar, col2_sidebar = st.sidebar.columns(2)
lista_mesi = list(range(1, 13))
selected_mese = col1_sidebar.selectbox(
    "Mese", lista_mesi, index=idx_mese_default,
    format_func=lambda x: calendar.month_name[x], key="selectbox_mese"
)
st.session_state.selected_mese_index = lista_mesi.index(selected_mese)
lista_anni = list(range(oggi.year - 2, oggi.year + 5))
selected_anno = col2_sidebar.selectbox(
    "Anno", lista_anni, index=idx_anno_default, key="selectbox_anno"
)
st.session_state.selected_anno_index = lista_anni.index(selected_anno)
nome_mese_corrente = calendar.month_name[selected_mese]

# --- LOGICA PER GENERARE/AGGIORNARE IL DATAFRAME DEI TURNI ---
def genera_struttura_calendario(anno, mese, medici_selezionati):
    _, ultimo_giorno = calendar.monthrange(anno, mese)
    date_del_mese = pd.date_range(start=f"{anno}-{mese:02d}-01", end=f"{anno}-{mese:02d}-{ultimo_giorno}")
    try:
        festivita_anno = holidays.country_holidays("IT", years=anno)
    except KeyError:
        st.warning(f"Festività per l'anno {anno} non disponibili.")
        festivita_anno = {}
    df_cols = {
        COL_DATA: date_del_mese,
        COL_GIORNO: [d.strftime("%A") for d in date_del_mese],
        COL_FESTIVO: [d.date() in festivita_anno for d in date_del_mese], # Colonna booleana
        COL_NOME_FESTIVO: [festivita_anno.get(d.date(), "") for d in date_del_mese]
    }
    for medico in medici_selezionati:
        df_cols[medico] = "Presente"
    return pd.DataFrame(df_cols)

calendar_config_key = f"{selected_anno}-{selected_mese}-{'_'.join(sorted(medici_pianificati))}"
if 'current_calendar_config_key' not in st.session_state or \
   st.session_state.current_calendar_config_key != calendar_config_key or \
   'df_turni' not in st.session_state:
    if medici_pianificati:
        st.session_state.df_turni = genera_struttura_calendario(selected_anno, selected_mese, medici_pianificati)
    else:
        st.session_state.df_turni = pd.DataFrame()
    st.session_state.current_calendar_config_key = calendar_config_key

# --- FUNZIONE DI STYLING ---
def evidenzia_weekend_festivi(row):
    data_val = row[COL_DATA] # Oggetto Timestamp
    is_weekend = data_val.weekday() >= 5 # Sabato o Domenica
    # COL_FESTIVO è una colonna booleana nel DataFrame originale
    color = "background-color: #f0f0f0" if is_weekend or row[COL_FESTIVO] else ""
    return [color] * len(row)

# --- VISUALIZZAZIONE E MODIFICA CALENDARIO ---
st.header(f"🗓️ Pianificazione Turni per {nome_mese_corrente} {selected_anno}")

if not medici_pianificati:
    st.info("👈 Seleziona almeno un medico dalla sidebar per iniziare la pianificazione.")
elif 'df_turni' in st.session_state and not st.session_state.df_turni.empty:
    
    # 1. VISUALIZZAZIONE STILIZZATA (NON EDITABILE)
    st.markdown("#### ✨ Visualizzazione Calendario")
    df_visualizzazione = st.session_state.df_turni.copy()
    
    # Applica lo styling e la formattazione al DataFrame completo
    styled_df = df_visualizzazione.style \
        .apply(evidenzia_weekend_festivi, axis=1) \
        .format({COL_DATA: lambda dt: dt.strftime('%d/%m/%Y (%a)')})

    # Nascondi la colonna COL_FESTIVO (booleana) dalla visualizzazione finale
    # perché l'informazione è già data dal colore e da COL_NOME_FESTIVO.
    styled_df = styled_df.hide(columns=[COL_FESTIVO], axis=1)
    
    st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True
    )
    st.markdown("---")

    # 2. EDITOR DATI per le ASSENZE
    st.markdown("#### 📝 Inserisci/Modifica Assenze")
    # Colonne per l'editor: Data e Giorno per contesto, più le colonne dei medici per l'input.
    cols_per_editor = [COL_DATA, COL_GIORNO] + medici_pianificati
    
    column_config_editor = {
        COL_DATA: st.column_config.DateColumn("Data", format="DD/MM/YYYY", disabled=True, width="small"),
        COL_GIORNO: st.column_config.TextColumn("Giorno", disabled=True, width="small"),
    }
    for medico in medici_pianificati:
        column_config_editor[medico] = st.column_config.SelectboxColumn(
            f"Dr. {medico.split()[-1] if len(medico.split()) > 1 else medico}",
            help=f"Stato di {medico}", options=TIPI_ASSENZA, required=True, width="medium"
        )
    
    # Passa solo le colonne necessarie all'editor
    df_editor_input = st.session_state.df_turni[cols_per_editor].copy()
    
    edited_df_assenze = st.data_editor(
        df_editor_input,
        column_config=column_config_editor,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"data_editor_assenze_{calendar_config_key}" 
    )

    # Aggiorna il DataFrame principale in session_state se ci sono state modifiche nelle assenze
    modifiche_effettuate = False
    for medico_col in medici_pianificati: # Itera solo sulle colonne dei medici modificate nell'editor
        if medico_col in st.session_state.df_turni.columns and medico_col in edited_df_assenze.columns:
            if not st.session_state.df_turni[medico_col].equals(edited_df_assenze[medico_col]):
                st.session_state.df_turni[medico_col] = edited_df_assenze[medico_col]
                modifiche_effettuate = True
    
    if modifiche_effettuate:
        st.toast("Assenze aggiornate.", icon="📝")
        # st.rerun() # Di solito non necessario, Streamlit dovrebbe rieseguire la parte di visualizzazione

    # --- ESPORTAZIONE ---
    st.markdown("---")
    def to_excel(df_to_export):
        output_buffer = BytesIO()
        df_copy = df_to_export.copy() # Lavora su una copia
        # Assicurati che COL_DATA sia datetime per Excel
        if COL_DATA in df_copy.columns:
            if not pd.api.types.is_datetime64_any_dtype(df_copy[COL_DATA]):
                try: df_copy[COL_DATA] = pd.to_datetime(df_copy[COL_DATA])
                except Exception: pass
        
        with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
            df_copy.to_excel(writer, index=False, sheet_name=f"Turni_{selected_anno}_{selected_mese:02}")
        output_buffer.seek(0)
        return output_buffer

    if not st.session_state.df_turni.empty:
        # Esporta il DataFrame completo da session_state, che include tutte le colonne
        excel_bytes = to_excel(st.session_state.df_turni.copy()) 
        st.download_button(
            label="📥 Scarica Calendario in Excel",
            data=excel_bytes,
            file_name=f"turni_{selected_anno}_{selected_mese:02d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_excel_button"
        )
    else:
        st.caption("Nessun dato da esportare.")

elif medici_pianificati and ('df_turni' not in st.session_state or st.session_state.df_turni.empty):
    st.warning("Il DataFrame dei turni è vuoto. Verifica selezioni o ricarica.")

st.sidebar.markdown("---")
st.sidebar.caption(f"Versione 0.8 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
