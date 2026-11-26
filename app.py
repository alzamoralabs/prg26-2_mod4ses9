from dataclasses import dataclass

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool, ToolRuntime
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

load_dotenv()  # OPENAI_API_KEY en tu .env

# ---------- Contexto de ejecución (quién es el usuario) ----------
@dataclass
class Context:
    user_id: str


# ---------- Tools que leen/escriben la memoria de largo plazo ----------
@tool
def guardar_dato_usuario(clave: str, valor: str, runtime: ToolRuntime[Context]) -> str:
    """Guarda un dato o preferencia duradera del usuario.
    Ejemplos: clave='nombre', valor='Ana'; clave='dieta', valor='vegetariana'."""
    namespace = ("usuarios", runtime.context.user_id)
    runtime.store.put(namespace, clave, {"valor": valor})
    return f"Guardado: {clave} = {valor}"


@tool
def consultar_perfil_usuario(runtime: ToolRuntime[Context]) -> str:
    """Devuelve todo lo que se sabe del usuario de conversaciones anteriores."""
    namespace = ("usuarios", runtime.context.user_id)
    items = runtime.store.search(namespace)
    if not items:
        return "No hay información previa de este usuario."
    return "\n".join(f"- {i.key}: {i.value['valor']}" for i in items)


# ---------- Agente ----------
model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
# Alternativa local:
# from langchain_ollama import ChatOllama
# model = ChatOllama(model="llama3.2", temperature=0)

agent = create_agent(
    model=model,
    tools=[guardar_dato_usuario, consultar_perfil_usuario],
    system_prompt=(
        "Eres un asistente amable. Cuando el usuario comparta datos personales "
        "o preferencias duraderas, guárdalos con guardar_dato_usuario. "
        "Si te preguntan qué sabes de ellos, o al iniciar una conversación nueva, "
        "usa consultar_perfil_usuario antes de responder."
    ),
    checkpointer=InMemorySaver(),   # corto plazo (por thread_id)
    store=InMemoryStore(),          # largo plazo (por namespace)
    context_schema=Context,
)


def preguntar(texto: str, thread_id: str, user_id: str = "ana") -> str:
    resp = agent.invoke(
        {"messages": [{"role": "user", "content": texto}]},
        config={"configurable": {"thread_id": thread_id}},
        context=Context(user_id=user_id),
    )
    return resp["messages"][-1].content


# ---------- Demo ----------
# 1) Conversación A: corto plazo (el historial vive en el thread)
print(preguntar("Hola, soy Ana y soy vegetariana. Recuérdalo.", thread_id="conv-A"))
print(preguntar("¿Cómo me llamo?", thread_id="conv-A"))          # responde por el historial

# 2) Conversación B (thread nuevo): sin historial, pero con largo plazo
print(preguntar("¿Qué sabes de mí?", thread_id="conv-B"))        # lo recupera del store

# 3) Otro usuario: namespace distinto, no ve los datos de Ana
print(preguntar("¿Qué sabes de mí?", thread_id="conv-C", user_id="luis"))