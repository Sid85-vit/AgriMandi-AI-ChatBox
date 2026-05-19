# 🌱 Agri Mandi Chatbot

A local, AI-powered dashboard that tracks real-time Indian agricultural commodity prices using the official data.gov.in API and local LLMs.

## ⚠️ Prerequisites

Before running this project, you must have two things installed on your computer:
1. **Python 3.8+**
2. **[Ollama](https://ollama.com/)** ## 🚀 Setup Instructions

**1. Clone the repository and install dependencies:**
Open your terminal inside the project folder and run:
`pip install -r requirements.txt`

**2. Launch Ollama with CORS Allowed (Crucial for Chat!)**
By default, modern browsers block the website from talking to Ollama due to cross-origin security rules. You *must* launch Ollama with cross-origin access enabled:

* **On Windows:**
  1. Quit Ollama from your taskbar tray (bottom-right corner icon).
  2. Open Command Prompt and run: `set OLLAMA_ORIGINS=*`
  3. Start Ollama in that same window: `ollama serve`
  4. In a separate terminal tab, pull the model weights if you haven't already: `ollama pull phi3`

* **On Mac/Linux:**
  1. Close Ollama completely.
  2. Open Terminal and run: `OLLAMA_ORIGINS="*" ollama serve`
  3. In a separate terminal window, run: `ollama pull phi3`

**3. Setup your API Key:**
* Ask me (the repository owner) for the secure API key via Slack/Teams.
* Create a new file named exactly `.env` in the root folder of this project.
* Add the key to the file like this: `GOV_API_KEY=the_key_I_sent_you`

**4. Run the Backend Server:**
In a separate terminal window, start your FastAPI backend:
`python server.py`

**5. Launch the App:**
Simply double-click the `index.html` file to open it in your browser. The app will automatically sync with the government servers and your local Ollama instance.
