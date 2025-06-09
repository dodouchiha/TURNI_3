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
COL_FESTIVO = "Festivo"
COL_NOME_FESTIVO = "Nome Festivo"
COL_AMBULATORIO = "Ambulatorio"
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
    """Carica l'elenco dei medici da GitHub."""
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
    """Salva l'elenco dei medici su GitHub."""
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
        
        if res.status_code == 200 or res.status_code == 201:
            st.session_state.sha_medici = res.json()["content"]["sha"]
            return True
        return False
    except requests.exceptions.HTTPError as e:
        st.error(f"Errore GitHub (salva): {e.response.status_code} - {e.response.text}")
        try:
            st.error(f"Dettagli errore: {e.response.json()}")
        except ValueError:
            pass
        return False
    except Exception as e:
        st.error(f"Errore imprevisto durante il salvataggio dei medici: {e}")
        return False

# --- INIZIALIZZAZIONE STATO ---
if 'elenco_medici_completo' not in st.session_state:
    st.session_state.elenco_medici_completo = carica_medici()
if 'sha_medici' not in st.session_state:
    st.session_state.sha_medici = None

# --- GESTIONE MEDICI - Sidebar ---
st.sidebar.header("👨‍⚕️ Gestione Medici")

with st.sidebar.form("form_aggiungi_medico"):
    nuovo_medico = st.text_input("➕ Aggiungi nuovo medico").strip()
    submitted_add = st.form_submit_button("Aggiungi Medico")

if submitted_add and nuovo_medico:
    if nuovo_medico not in st.session_state.elenco_medici_completo:
        st.session_state.elenco_medici_completo.append(nuovo_medico)
        st.session_state.elenco_medici_completo.sort()
        if salva_medici(st.session_state.elenco_medici_completo):
            st.sidebar.success(f"✅ Medico '{nuovo_medico}' aggiunto e salvato.")
            st.rerun() # MODIFICATO
        else:
            st.sidebar.error("❌ Errore nel salvataggio del medico su GitHub.")
            st.session_state.elenco_medici_completo.remove(nuovo_medico) # Ripristina
    else:
        st.sidebar.warning(f"'{nuovo_medico}' è già presente nell'elenco.")

if st.session_state.elenco_medici_completo:
    medico_da_rimuovere_options = [""] + st.session_state.elenco_medici_completo
    medico_da_rimuovere = st.sidebar.selectbox(
        "🗑️ Rimuovi medico",
        options=medico_da_rimuovere_options,
        index=0,
        key="selectbox_rimuovi_medico" # Aggiunta chiave per stabilità
    )
    if st.sidebar.button("Conferma Rimozione", key="button_conferma_rimozione") and medico_da_rimuovere:
        medici_temp = st.session_state.elenco_medici_completo.copy()
        medici_temp.remove(medico_da_rimuovere)
        if salva_medici(medici_temp):
            st.session_state.elenco_medici_completo = medici_temp
            st.sidebar.success(f"✅ Medico '{medico_da_rimuovere}' rimosso e salvato.")
            if 'medici_pianificati' in st.session_state and medico_da_rimuovere in st.session_state.medici_pianificati:
                st.session_state.medici_pianificati.remove(medico_da_rimuovere)
            if 'df_turni' in st.session_state:
                del st.session_state.df_turni
            # Resetta il selectbox di rimozione se il medico rimosso era selezionato (opzionale, ma buona UX)
            # Questo può essere complicato da gestire perfettamente senza un rerun che ricarichi i widget
            # La soluzione più semplice è il rerun
            st.rerun() # MODIFICATO
        else:
            st.sidebar.error("❌ Errore nella rimozione del medico su GitHub.")

st.sidebar.markdown("---")
medici_da_pianificare_default = st.session_state.get('medici_pianificati', st.session_state.elenco_medici_completo)
medici_pianificati = st.sidebar.multiselect(
    "✅ Seleziona medici da pianificare",
    options=st.session_state.elenco_medici_completo,
    default=medici_da_pianificare_default,
    key="multiselect_medici_pianificati"
)
# Aggiorna lo stato della sessione solo se la selezione cambia
if 'medici_pianificati' not in st.session_state or st.session_state.medici_pianificati != medici_pianificati:
    st.session_state.medici_pianificati = medici_pianificati
    # Se i medici pianificati cambiano, è necessario rigenerare df_turni
    if 'df_turni' in st.session_state:
        del st.session_state.df_turni # Forza la rigenerazione


# --- SELEZIONE MESE E ANNO ---
st.sidebar.markdown("---")
st.sidebar.header("🗓️ Selezione Periodo")
oggi = datetime.today()
col1_sidebar, col2_sidebar = st.sidebar.columns(2)
selected_mese = col1_sidebar.selectbox(
    "Mese",
    list(range(1, 13)),
    index=st.session_state.get('selected_mese_index', oggi.month - 1),
    format_func=lambda x: calendar.month_name[x],
    key="selectbox_mese"
)
st.session_state.selected_mese_index = list(range(1,13)).index(selected_mese)

selected_anno = col2_sidebar.selectbox(
    "Anno",
    list(range(oggi.year - 2, oggi.year + 5)),
    index=st.session_state.get('selected_anno_index', 2),
    key="selectbox_anno"
)
st.session_state.selected_anno_index = list(range(oggi.year - 2, oggi.year + 5)).index(selected_anno)
nome_mese_corrente = calendar.month_name[selected_mese]

# --- LOGICA PER GENERARE/AGGIORNARE IL DATAFRAME DEI TURNI ---
def genera_struttura_calendario(anno, mese, medici_selezionati):
    _, ultimo_giorno = calendar.monthrange(anno, mese)
    date_del_mese = pd.date_range(start=f"{anno}-{mese:02d}-01", end=f"{anno}-{mese:02d}-{ultimo_giorno}")
    
    try:
        festivita_anno = holidays.country_holidays("IT", years=anno)
    except KeyError:
        st.warning(f"Festività per l'anno {anno} non disponibili. Procedo senza.")
        festivita_anno = {}

    def is_giorno_ambulatorio(data_controllo):
        return data_controllo.weekday() in [0, 2, 4] and data_controllo.date() not in festivita_anno

    df_cols = {
        COL_DATA: date_del_mese,
        COL_GIORNO: [d.strftime("%A") for d in date_del_mese], # Più efficiente
        COL_FESTIVO: [d.date() in festivita_anno for d in date_del_mese], # Più efficiente
        COL_NOME_FESTIVO: [festivita_anno.get(d.date(), "") for d in date_del_mese],
        COL_AMBULATORIO: ["Ambulatorio" if is_giorno_ambulatorio(d) else "" for d in date_del_mese]
    }

    for medico in medici_selezionati:
        df_cols[medico] = "Presente"
    
    return pd.DataFrame(df_cols)

calendar_config_key = f"{selected_anno}-{selected_mese}-{'_'.join(sorted(medici_pianificati))}"

if 'current_calendar_config_key' not in st.session_state or \
   st.session_state.current_calendar_config_key != calendar_config_key or \
   'df_turni' not in st.session_state:
    
    st.session_state.df_turni = genera_struttura_calendario(selected_anno, selected_mese, medici_pianificati)
    st.session_state.current_calendar_config_key = calendar_config_key


# --- VISUALIZZAZIONE E MODIFICA CALENDARIO ---
st.header(f"🗓️ Pianificazione Turni per {nome_mese_corrente} {selected_anno}")

if not medici_pianificati:
    st.info("Nessun medico selezionato per la pianificazione. Selezionane almeno uno dalla sidebar.")
elif 'df_turni' in st.session_state and not st.session_state.df_turni.empty:
    
    column_config = {
        COL_DATA: st.column_config.DateColumn("Data", format="DD/MM/YYYY", disabled=True),
        COL_GIORNO: st.column_config.TextColumn("Giorno", disabled=True),
        COL_FESTIVO: st.column_config.CheckboxColumn("Festivo?", disabled=True),
        COL_NOME_FESTIVO: st.column_config.TextColumn("Festività", disabled=True),
        COL_AMBULATORIO: st.column_config.TextColumn("Ambulatorio", disabled=True),
    }
    for medico in medici_pianificati:
        column_config[medico] = st.column_config.SelectboxColumn(
            f"Assenza {medico}",
            options=TIPI_ASSENZA,
            required=True
        )
    
    st.markdown("#### Inserisci/Modifica Assenze:")
    
    # Usiamo una copia del df per l'editor, e aggiorniamo session_state solo se ci sono cambiamenti
    # Questo evita potenziali loop di rerun se l'editor stesso causa un rerun
    df_editor_input = st.session_state.df_turni.copy()
    
    edited_df = st.data_editor(
        df_editor_input,
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        key=f"data_editor_{calendar_config_key}" 
    )

    if not edited_df.equals(st.session_state.df_turni):
        st.session_state.df_turni = edited_df.copy() # Salva una copia
        # Non è necessario un rerun qui, perché st.data_editor gestisce l'aggiornamento
        # e vogliamo che le modifiche persistano immediatamente nello stato della sessione.

    # --- ESPORTAZIONE ---
    st.markdown("---")
    def to_excel(df_to_export):
        output_buffer = BytesIO()
        # Ensure 'Data' column is datetime if it's not already, for consistent Excel formatting
        if COL_DATA in df_to_export.columns and not pd.api.types.is_datetime64_any_dtype(df_to_export[COL_DATA]):
            try:
                df_to_export[COL_DATA] = pd.to_datetime(df_to_export[COL_DATA])
            except Exception as e:
                st.warning(f"Impossibile convertire la colonna Data in datetime per l'export: {e}")

        with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
            # Formattare la colonna Data come testo se si vuole preservare "GG/MM/YYYY" letteralmente
            # df_to_export[COL_DATA] = df_to_export[COL_DATA].dt.strftime('%d/%m/%Y')
            df_to_export.to_excel(writer, index=False, sheet_name=f"Turni_{selected_anno}_{selected_mese:02}")
        output_buffer.seek(0)
        return output_buffer

    if not st.session_state.df_turni.empty:
        excel_bytes = to_excel(st.session_state.df_turni.copy())
        st.download_button(
            label="📥 Scarica Calendario Attuale in Excel",
            data=excel_bytes,
            file_name=f"turni_{selected_anno}_{selected_mese:02d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_excel_button"
        )
    else:
        st.info("Nessun dato da esportare.")


else:
    if medici_pianificati:
        st.warning("Il DataFrame dei turni è vuoto. Prova a ricaricare o cambiare selezione.")

st.sidebar.markdown("---")
st.sidebar.caption(f"Versione 0.3 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
