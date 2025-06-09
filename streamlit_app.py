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
TIPI_ASSENZA = ["Presente", "Ferie", "Malattia", "Congresso", "Lezione", "Altro"]

# --- CONFIGURAZIONE GITHUB --- (invariata)
GITHUB_USER = "dodouchiha"
REPO_NAME = "turni_3"
FILE_PATH = "medici.json"
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN")

if not GITHUB_TOKEN:
    st.error("Token GitHub non configurato nelle secrets! L'applicazione non potrà salvare i dati.")
    st.stop()

API_URL = f"https://api.github.com/repos/{GITHUB_USER}/{REPO_NAME}/contents/{FILE_PATH}"
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

# --- FUNZIONI GITHUB --- (invariate, le ometto per brevità)
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
        st.sidebar.error(f"Errore GitHub (salva): {e.response.status_code}")
        print(f"Dettagli errore GitHub: {e.response.text}")
        try:
            print(f"Corpo errore JSON: {e.response.json()}")
        except ValueError:
            pass
        return False
    except Exception as e:
        st.sidebar.error(f"Errore imprevisto durante il salvataggio: {e}")
        print(f"Errore imprevisto (salva_medici): {e}")
        return False

# --- INIZIALIZZAZIONE STATO --- (invariata)
if 'elenco_medici_completo' not in st.session_state:
    st.session_state.elenco_medici_completo = carica_medici()
if 'sha_medici' not in st.session_state:
    st.session_state.sha_medici = None

# --- UI Sidebar --- (invariata)
st.sidebar.header("🛠️ Configurazione")
with st.sidebar.expander("👨‍⚕️ Gestione Medici", expanded=True):
    with st.form("form_aggiungi_medico", clear_on_submit=True):
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
                st.sidebar.error("Salvataggio del nuovo medico non riuscito.")
        else:
            st.warning(f"'{nuovo_medico}' è già presente nell'elenco.")

    if st.session_state.elenco_medici_completo:
        options_rimuovi = [""] + sorted(list(set(st.session_state.elenco_medici_completo)))
        current_selection_rimuovi = st.session_state.get("medico_da_rimuovere_selection", "")
        try:
            default_index_rimuovi = options_rimuovi.index(current_selection_rimuovi)
        except ValueError:
            default_index_rimuovi = 0
        medico_da_rimuovere = st.selectbox(
            "🗑️ Rimuovi medico",
            options=options_rimuovi,
            index=default_index_rimuovi,
            key="selectbox_rimuovi_medico_key"
        )
        st.session_state.medico_da_rimuovere_selection = medico_da_rimuovere
        if st.button("Conferma Rimozione", key="btn_rimuovi_medico") and medico_da_rimuovere:
            medici_temp = st.session_state.elenco_medici_completo.copy()
            medici_temp.remove(medico_da_rimuovere)
            if salva_medici(medici_temp):
                st.session_state.elenco_medici_completo = medici_temp
                st.toast(f"Medico '{medico_da_rimuovere}' rimosso.", icon="🗑️")
                if 'medici_pianificati' in st.session_state and medico_da_rimuovere in st.session_state.medici_pianificati:
                    st.session_state.medici_pianificati.remove(medico_da_rimuovere)
                if 'df_turni' in st.session_state:
                    del st.session_state.df_turni
                st.session_state.medico_da_rimuovere_selection = ""
                st.rerun()
            else:
                st.sidebar.error("Rimozione del medico non riuscita.")
    else:
        st.caption("Nessun medico nell'elenco.")

st.sidebar.markdown("---")
default_medici_pianificati = st.session_state.get('medici_pianificati', [])
default_medici_pianificati = [m for m in default_medici_pianificati if m in st.session_state.elenco_medici_completo]
if not default_medici_pianificati and st.session_state.elenco_medici_completo:
    default_medici_pianificati = st.session_state.elenco_medici_completo.copy()
medici_pianificati = st.sidebar.multiselect(
    "✅ Seleziona medici da pianificare",
    options=sorted(list(set(st.session_state.elenco_medici_completo))),
    default=default_medici_pianificati,
    key="multiselect_medici_pianificati"
)
if 'medici_pianificati' not in st.session_state or \
   set(st.session_state.medici_pianificati) != set(medici_pianificati):
    st.session_state.medici_pianificati = medici_pianificati
    if 'df_turni' in st.session_state:
        del st.session_state.df_turni

st.sidebar.markdown("---")
st.sidebar.header("🗓️ Selezione Periodo")
oggi = datetime.today()
idx_mese_default = st.session_state.get('selected_mese_index', oggi.month - 1)
idx_anno_default = st.session_state.get('selected_anno_index', 2)
col1_sidebar, col2_sidebar = st.sidebar.columns(2)
lista_mesi = list(range(1, 13))
selected_mese = col1_sidebar.selectbox(
    "Mese",
    lista_mesi,
    index=idx_mese_default,
    format_func=lambda x: calendar.month_name[x],
    key="selectbox_mese"
)
st.session_state.selected_mese_index = lista_mesi.index(selected_mese)
lista_anni = list(range(oggi.year - 2, oggi.year + 5))
selected_anno = col2_sidebar.selectbox(
    "Anno",
    lista_anni,
    index=idx_anno_default,
    key="selectbox_anno"
)
st.session_state.selected_anno_index = lista_anni.index(selected_anno)
nome_mese_corrente = calendar.month_name[selected_mese]

# --- LOGICA PER GENERARE/AGGIORNARE IL DATAFRAME DEI TURNI --- (invariata)
def genera_struttura_calendario(anno, mese, medici_selezionati):
    _, ultimo_giorno = calendar.monthrange(anno, mese)
    date_del_mese = pd.date_range(start=f"{anno}-{mese:02d}-01", end=f"{anno}-{mese:02d}-{ultimo_giorno}")
    
    try:
        festivita_anno = holidays.country_holidays("IT", years=anno)
    except KeyError:
        st.warning(f"Festività per l'anno {anno} non disponibili. Procedo senza.")
        festivita_anno = {}

    df_cols = {
        COL_DATA: date_del_mese, # MANTENERE COME OGGETTI DATETIME
        COL_GIORNO: [d.strftime("%A") for d in date_del_mese],
        COL_FESTIVO: [d.date() in festivita_anno for d in date_del_mese],
        COL_NOME_FESTIVO: [festivita_anno.get(d.date(), "") for d in date_del_mese],
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

# --- FUNZIONE DI STYLING PER st.dataframe ---
def style_weekend_festivi(row):
    color = 'background-color: #f0f0f0' 
    default_style = '' 
    
    # La colonna COL_DATA dovrebbe essere già un oggetto Timestamp qui
    # Non c'è più bisogno di pd.to_datetime() o .date() se row[COL_DATA] è già un Timestamp
    # Pandas Styler passa una Series per riga, quindi row[COL_DATA] è il valore della cella.
    data_val = row[COL_DATA]
    
    # Assicuriamoci che sia un tipo di data che possiamo usare con weekday()
    if not isinstance(data_val, (datetime, pd.Timestamp)):
         # Questo non dovrebbe accadere se la colonna è stata creata correttamente.
         # Ma per sicurezza, proviamo a convertirla. Se fallisce, non applichiamo lo stile.
        try:
            data_val = pd.to_datetime(data_val)
        except:
            return [default_style] * len(row) # Non fare nulla se non è una data valida

    is_weekend = data_val.weekday() >= 5 # 5 per Sabato, 6 per Domenica
    
    if row[COL_FESTIVO] or is_weekend:
        return [color] * len(row)
    return [default_style] * len(row)

# --- VISUALIZZAZIONE E MODIFICA CALENDARIO ---
st.header(f"🗓️ Pianificazione Turni per {nome_mese_corrente} {selected_anno}")

if not medici_pianificati:
    st.info("👈 Seleziona almeno un medico dalla sidebar per iniziare la pianificazione.")
elif 'df_turni' in st.session_state and not st.session_state.df_turni.empty:
    
    st.markdown("#### ✨ Visualizzazione Calendario (con festivi evidenziati)")
    df_display = st.session_state.df_turni.copy()
    
    # NON convertire df_display[COL_DATA] a stringa qui.
    # Lo styling funziona meglio con oggetti datetime.
    # Applicheremo un formattatore specifico a st.dataframe.
    
    cols_to_display = [col for col in df_display.columns if col != COL_FESTIVO]
    
    st.dataframe(
        df_display[cols_to_display].style.apply(style_weekend_festivi, axis=1)
                                      .format(formatter={COL_DATA: lambda dt: dt.strftime('%d/%m/%Y (%a)')}), # APPLICA FORMATTER QUI
        use_container_width=True,
        hide_index=True
    )
    st.markdown("---")

    st.markdown("#### 📝 Inserisci/Modifica Assenze")
    column_config = {
        COL_DATA: st.column_config.DateColumn("Data", format="DD/MM/YYYY", disabled=True, width="small"),
        COL_GIORNO: st.column_config.TextColumn("Giorno", disabled=True, width="small"),
        COL_FESTIVO: st.column_config.CheckboxColumn("Festivo?", disabled=True, width="small"),
        COL_NOME_FESTIVO: st.column_config.TextColumn("Festività", disabled=True, width="medium"),
    }
    for medico in medici_pianificati:
        column_config[medico] = st.column_config.SelectboxColumn(
            f"Dr. {medico.split()[-1] if len(medico.split()) > 1 else medico}",
            help=f"Stato di {medico}",
            options=TIPI_ASSENZA,
            required=True,
            width="medium"
        )
    
    df_editor_input = st.session_state.df_turni.copy()
    
    edited_df = st.data_editor(
        df_editor_input,
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"data_editor_{calendar_config_key}" 
    )

    if not edited_df.equals(st.session_state.df_turni):
        st.session_state.df_turni = edited_df.copy()

    # --- ESPORTAZIONE --- (invariata)
    st.markdown("---")
    def to_excel(df_to_export):
        output_buffer = BytesIO()
        df_copy = df_to_export.copy()
        if COL_DATA in df_copy.columns:
            try:
                # Assicurarsi che sia datetime per il formato Excel, se non lo è già
                if not pd.api.types.is_datetime64_any_dtype(df_copy[COL_DATA]):
                    df_copy[COL_DATA] = pd.to_datetime(df_copy[COL_DATA])
            except Exception:
                pass
        with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
            df_copy.to_excel(writer, index=False, sheet_name=f"Turni_{selected_anno}_{selected_mese:02}")
        output_buffer.seek(0)
        return output_buffer

    if not st.session_state.df_turni.empty:
        excel_bytes = to_excel(st.session_state.df_turni)
        st.download_button(
            label="📥 Scarica Calendario in Excel",
            data=excel_bytes,
            file_name=f"turni_{selected_anno}_{selected_mese:02d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_excel_button"
        )
    else:
        st.caption("Nessun dato da esportare al momento.")

elif medici_pianificati and ('df_turni' not in st.session_state or st.session_state.df_turni.empty):
    st.warning("Il DataFrame dei turni è vuoto o non ancora generato. Verifica le selezioni o prova a ricaricare.")

st.sidebar.markdown("---")
st.sidebar.caption(f"Versione 0.6 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
