import streamlit as st
import pandas as pd
import calendar
from datetime import datetime
import holidays
from io import BytesIO
import requests
import json
import base64

# Configurazione iniziale
st.set_page_config(page_title="Gestione Turni", layout="wide")

# CONFIGURAZIONE GITHUB
GITHUB_USER = "dodouchiha"
REPO_NAME = "turni_3"
FILE_PATH = "medici.json"
GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
API_URL = f"https://api.github.com/repos/{GITHUB_USER}/{REPO_NAME}/contents/{FILE_PATH}"

# ----------------- FUNZIONI GITHUB ------------------
def carica_medici():
    res = requests.get(API_URL, headers={"Authorization": f"token {GITHUB_TOKEN}"})
    if res.status_code == 200:
        contenuto = res.json()
        file_sha = contenuto["sha"]
        elenco = json.loads(requests.get(contenuto["download_url"]).text)
        return elenco, file_sha
    else:
        return [], None

def salva_medici(lista, sha=None):
    if sha is None:
        st.error("❌ SHA non disponibile: impossibile aggiornare un file esistente.")
        return False

    blob = json.dumps(lista, indent=2).encode()
    encoded = base64.b64encode(blob).decode()

    # Debug persistente nella pagina
    st.markdown("### 🛠️ Debug GitHub API")
    st.text(f"SHA usato: {sha}")
    st.text(f"Medici inviati: {lista}")
    st.text(f"Base64 (lunghezza): {len(encoded)}")
    st.code(blob.decode()[:500], language='json')

    dati = {
        "message": f"Update elenco medici - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": encoded,
        "branch": "main",
        "sha": sha
    }

    res = requests.put(API_URL, headers={"Authorization": f"token {GITHUB_TOKEN}"}, json=dati)
    st.write(f"➡️ Risposta GitHub: {res.status_code}")
    st.code(res.text, language='json')

    return res.status_code in [200, 201]

# ----------------- LOGICA APP -----------------------
elenco_medici, sha_medici = carica_medici()

# GESTIONE MEDICI - Sidebar
st.sidebar.header("👨‍⚕️ Gestione Medici")

with st.sidebar.form("form_aggiungi"):
    nuovo_medico = st.text_input("➕ Aggiungi medico")
    if st.form_submit_button("Aggiungi") and nuovo_medico:
        if nuovo_medico not in elenco_medici:
            elenco_medici.append(nuovo_medico)
            successo = salva_medici(elenco_medici, sha_medici)
            if successo:
                elenco_medici, sha_medici = carica_medici()
                st.success("✅ Medico aggiunto e salvato su GitHub.")
                st.experimental_rerun()
            else:
                st.sidebar.error("❌ Errore: salvataggio su GitHub non riuscito.")
        else:
            st.sidebar.warning("Medico già presente.")

medico_da_rimuovere = st.sidebar.selectbox("🗑️ Rimuovi medico", [""] + elenco_medici)
if medico_da_rimuovere and st.sidebar.button("Rimuovi"):
    elenco_medici.remove(medico_da_rimuovere)
    successo = salva_medici(elenco_medici, sha_medici)
    if successo:
        elenco_medici, sha_medici = carica_medici()
        st.success("✅ Medico rimosso e salvato su GitHub.")
        st.experimental_rerun()
    else:
        st.sidebar.error("❌ Errore: salvataggio su GitHub non riuscito.")

medici = st.sidebar.multiselect("✅ Seleziona medici da pianificare", elenco_medici, default=elenco_medici)

# SELEZIONE MESE E ANNO
oggi = datetime.today()
mese = st.selectbox("📅 Seleziona mese", list(range(1, 13)), index=oggi.month - 1, format_func=lambda x: calendar.month_name[x])
anno = st.selectbox("📆 Seleziona anno", list(range(oggi.year - 1, oggi.year + 3)), index=1)
nome_mese = calendar.month_name[mese]

# GENERA CALENDARIO
_, ultimo_giorno = calendar.monthrange(anno, mese)
date_mese = pd.date_range(start=f"{anno}-{mese:02d}-01", end=f"{anno}-{mese:02d}-{ultimo_giorno}")
festivi = holidays.country_holidays("IT", years=anno)

def is_ambulatorio(d):
    return d.weekday() in [0, 2, 4] and d.date() not in festivi

df = pd.DataFrame({
    "Data": date_mese,
    "Giorno": date_mese.strftime("%A"),
    "Festivo": pd.Series(date_mese).dt.date.isin(festivi),
    "Nome Festivo": [festivi.get(d.date(), "") for d in date_mese],
    "Mattina": "",
    "Pomeriggio": "",
    "Notte": "",
    "Riposo": "",
    "Ambulatorio": ["Ambulatorio" if is_ambulatorio(d) else "" for d in date_mese]
})

# ASSENZE PER MEDICO
tipi_assenza = ["Nessuna", "Ferie", "Congresso", "Lezione"]
for medico in medici:
    df[medico] = "Nessuna"
    st.markdown(f"### 📋 Assenze per {medico}")
    for i in range(len(df)):
        scelta = st.selectbox(f"{df.at[i, 'Data'].strftime('%d %b')} - {medico}", tipi_assenza, index=0, key=f"{medico}_{i}")
        df.at[i, medico] = scelta

# STILE
def evidenzia(row):
    style = []
    is_we = row["Data"].weekday() in [5, 6]
    for col in row.index:
        if col == "Ambulatorio" and row[col] != "Ambulatorio":
            style.append("background-color: black; color: white; border: 1px solid black")
        elif col in medici and row[col] != "Nessuna":
            style.append("background-color: lightblue; border: 1px solid black")
        elif row["Festivo"] or is_we:
            style.append("background-color: lightgray; border: 1px solid black")
        else:
            style.append("background-color: white; border: 1px solid black")
    return style

st.subheader(f"📅 Calendario {nome_mese} {anno}")
st.dataframe(df.style.apply(evidenzia, axis=1), use_container_width=True)

# ESPORTAZIONE
def to_excel(df):
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Turni")
    buffer.seek(0)
    return buffer

st.download_button("📅 Scarica Excel", data=to_excel(df), file_name=f"turni_{anno}_{mese:02d}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
