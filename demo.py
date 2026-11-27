from myprompts import SYSTEM_PROMPT, AGENDA
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from dotenv import load_dotenv
load_dotenv()  # OPENAI_API_KEY en tu .env

llm = ChatOllama(model="llama3.2", temperature=0.2, verbose=True)
# response = llm.invoke(SYSTEM_PROMPT + " ASUME QUE ERES DOLORES EL AGENTE DESCRITO ANTES. EN BASE A ESTO AGENDA UNA CITA DE CARDIOLOGIA PARA EL PACIENTE JUAN PEREZ, QUE SUFRIÓ UN INFARTO HACE 3 MESES Y TIENE ANTECEDENTES DE HIPERTENSIÓN. EL PACIENTE NECESITA UNA REVISIÓN DE SU ESTADO CARDÍACO Y POSIBLE AJUSTE DE MEDICACIÓN. POR FAVOR, SUGIERE FECHAS DISPONIBLES PARA LA CITA Y CONFIRMA LA DISPONIBILIDAD DEL CARDIÓLOGO. " + AGENDA)
# print(response)

# while True:
#     user_input = input("You: ")
#     if user_input.lower() in ["exit", "quit"]:
#         break
#     response = llm.invoke(user_input)
#     print("Dolores: " + str(response))

from langchain_openai import ChatOpenAI
from langchain.tools import tool, ToolRuntime
import os

@tool
def guardar_dato_usuario(clave: str, valor: str, runtime: ToolRuntime)-> str:
    """Guarda un dato o preferencia duradera del usuario.
    Ejemplos: clave='nombre', valor='Ana'; clave='dieta', valor='vegetariana'."""
    namespace = ("usuarios", runtime.context.user_id)
    runtime.store.put(namespace, clave, {"valor": valor})
    return f"Guardado: {clave} = {valor}"

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, api_key=os.getenv("OPENAI_API_KEY"))
agent = create_agent(model=llm, tools=[guardar_dato_usuario],
                     system_prompt="Eres un asistente amable. Cuando el usuario comparta datos personales o preferencias duraderas, guárdalos con guardar_dato_usuario." \
                     "Si te preguntan qué sabes de ellos, o al iniciar una conversación nueva, usa consultar_perfil_usuario antes de responder.")

while True:
    user_input = input("You: ")
    if user_input.lower() in ["exit", "quit"]:
        break
    response = agent.invoke({"input": user_input, "context": {"user_id": "ana"}})
    print("Agent: " + str(response))