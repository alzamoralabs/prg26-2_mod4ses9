"""
Memoria semántica, episódica y procedural en un agente LangChain (1.x)

Extiende el ejemplo de memoria corto/largo plazo:
  - Corto plazo  -> checkpointer + thread_id            (igual que antes)
  - Largo plazo  -> store, ahora dividido en 3 tipos:

    SEMÁNTICA   "qué sé"          Hechos sueltos sobre el usuario. Se buscan por significado.
    EPISÓDICA   "qué viví"        Experiencias pasadas (situación -> acción -> resultado -> lección).
                                  Se recuperan por similitud y se inyectan como ejemplos.
    PROCEDURAL  "cómo debo actuar" Instrucciones/reglas de comportamiento. El agente las reescribe
                                  cuando recibe feedback y se inyectan en el system prompt.
"""
import uuid
from dataclasses import dataclass
from datetime import date

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain.tools import ToolRuntime, tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

load_dotenv()  # OPENAI_API_KEY en tu .env

# ======================================================================
# 1. Store con búsqueda semántica (embeddings)
# ======================================================================
# Todo item que quiera ser buscable por significado debe tener un campo "texto".
store = InMemoryStore(
    index={
        "embed": OpenAIEmbeddings(model="text-embedding-3-small"),
        "dims": 1536,
        "fields": ["texto"],
    }
)

SEMANTICA = "semantica"
EPISODICA = "episodica"
PROCEDURAL = "procedural"


def ns(tipo: str, user_id: str) -> tuple[str, str]:
    """Un namespace por tipo de memoria y por usuario."""
    return (tipo, user_id)


@dataclass
class Context:
    user_id: str


# ======================================================================
# 2. PROCEDURAL: instrucciones que el agente aprende y se auto-modifica
# ======================================================================
INSTRUCCIONES_BASE = (
    "Eres un tutor de programación amable. Responde de forma clara y breve. "
    "Si el usuario comparte datos personales duraderos, guárdalos con guardar_hecho."
)


def obtener_instrucciones(store_, user_id: str) -> str:
    item = store_.get(ns(PROCEDURAL, user_id), "instrucciones")
    return item.value["texto"] if item else INSTRUCCIONES_BASE


@tool
def actualizar_instrucciones(nuevas_instrucciones: str, runtime: ToolRuntime[Context]) -> str:
    """Reescribe las instrucciones de comportamiento del agente para este usuario.
    Úsala cuando el usuario pida CÓMO quiere que respondas (formato, tono, longitud,
    idioma, orden de la respuesta). Debes enviar el texto COMPLETO de las instrucciones,
    integrando las instrucciones actuales con el nuevo feedback (no solo lo nuevo)."""
    runtime.store.put(
        ns(PROCEDURAL, runtime.context.user_id),
        "instrucciones",
        {"texto": nuevas_instrucciones},
        index=False,  # no hace falta buscarlas: siempre se cargan completas
    )
    return "Instrucciones actualizadas."


# ======================================================================
# 3. SEMÁNTICA: hechos sobre el usuario, buscables por significado
# ======================================================================
@tool
def guardar_hecho(hecho: str, runtime: ToolRuntime[Context]) -> str:
    """Guarda un hecho duradero sobre el usuario, en una frase autocontenida.
    Ejemplos: 'Se llama Ana', 'Trabaja con Python 3.12 en Windows',
    'Está aprendiendo LangGraph'."""
    runtime.store.put(
        ns(SEMANTICA, runtime.context.user_id),
        str(uuid.uuid4()),
        {"texto": hecho},
    )
    return f"Guardado: {hecho}"


@tool
def buscar_hechos(consulta: str, runtime: ToolRuntime[Context]) -> str:
    """Busca hechos conocidos del usuario relacionados con la consulta."""
    items = runtime.store.search(ns(SEMANTICA, runtime.context.user_id), query=consulta, limit=5)
    if not items:
        return "No hay hechos guardados sobre este usuario."
    return "\n".join(f"- {i.value['texto']}" for i in items)


# ======================================================================
# 4. EPISÓDICA: experiencias pasadas, se inyectan como ejemplos (few-shot)
# ======================================================================
class Episodio(BaseModel):
    situacion: str = Field(description="Qué necesitaba o preguntó el usuario (1-2 frases).")
    accion: str = Field(description="Qué hizo o recomendó el asistente.")
    resultado: str = Field(description="Cómo terminó: ¿funcionó? ¿qué dijo el usuario?")
    leccion: str = Field(description="Qué conviene recordar si vuelve a pasar algo parecido.")


UMBRAL_EPISODIO = 0.25  # similitud mínima para inyectar un episodio (ajústalo según tu embedding)


def consolidar_episodio(agent, model, thread_id: str, user_id: str) -> Episodio:
    """Convierte una conversación terminada en un episodio y lo guarda.
    Es la 'reflexión' que ocurre DESPUÉS de la experiencia (no en cada turno)."""
    estado = agent.get_state({"configurable": {"thread_id": thread_id}})
    transcript = "\n".join(
        f"{m.type}: {m.content}"
        for m in estado.values["messages"]
        if m.type in ("human", "ai") and m.content
    )
    episodio = model.with_structured_output(Episodio).invoke(
        "Resume esta conversación como un episodio reutilizable:\n\n" + transcript
    )
    store.put(
        ns(EPISODICA, user_id),
        str(uuid.uuid4()),
        {
            "texto": episodio.situacion,  # campo que se embebe y se busca
            **episodio.model_dump(),
            "fecha": date.today().isoformat(),
        },
    )
    return episodio


# ======================================================================
# 5. System prompt dinámico: inyecta procedural + episodios relevantes
# ======================================================================
def ultimo_mensaje_usuario(mensajes) -> str:
    for m in reversed(mensajes):
        if m.type == "human":
            return str(m.content)
    return ""


@dynamic_prompt
def prompt_con_memoria(request: ModelRequest) -> str:
    store_ = request.runtime.store
    user_id = request.runtime.context.user_id

    # Procedural: siempre se carga completa
    prompt = obtener_instrucciones(store_, user_id)

    # Episódica: solo los episodios parecidos a lo que se pregunta ahora
    consulta = ultimo_mensaje_usuario(request.state["messages"])
    episodios = (
        store_.search(ns(EPISODICA, user_id), query=consulta, limit=2) if consulta else []
    )
    episodios = [e for e in episodios if (e.score or 0) >= UMBRAL_EPISODIO]
    if episodios:
        prompt += "\n\nExperiencias pasadas con este usuario que pueden ayudarte:\n"
        for e in episodios:
            v = e.value
            prompt += (
                f"- Situación: {v['situacion']}\n"
                f"  Qué se hizo: {v['accion']}\n"
                f"  Resultado: {v['resultado']}\n"
                f"  Lección: {v['leccion']}\n"
            )
    prompt += (
        "\n\nUsa buscar_hechos si necesitas datos del usuario. "
        "Si el usuario te pide cambiar tu forma de responder, usa actualizar_instrucciones."
    )
    return prompt


# ======================================================================
# 6. Agente
# ======================================================================
model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
# Alternativa local (los embeddings seguirían siendo de OpenAI salvo que uses OllamaEmbeddings):
# from langchain_ollama import ChatOllama
# model = ChatOllama(model="llama3.2", temperature=0)

agent = create_agent(
    model=model,
    tools=[guardar_hecho, buscar_hechos, actualizar_instrucciones],
    middleware=[prompt_con_memoria],
    checkpointer=InMemorySaver(),  # corto plazo
    store=store,                   # largo plazo (semántica + episódica + procedural)
    context_schema=Context,
)


def preguntar(texto: str, thread_id: str, user_id: str = "ana") -> str:
    resp = agent.invoke(
        {"messages": [{"role": "user", "content": texto}]},
        config={"configurable": {"thread_id": thread_id}},
        context=Context(user_id=user_id),
    )
    return resp["messages"][-1].content


def mostrar_memorias(user_id: str) -> None:
    print(f"\n===== MEMORIAS DE {user_id.upper()} =====")
    print("[procedural]\n ", obtener_instrucciones(store, user_id))
    print("[semántica]")
    for i in store.search(ns(SEMANTICA, user_id)):
        print("  -", i.value["texto"])
    print("[episódica]")
    for i in store.search(ns(EPISODICA, user_id)):
        v = i.value
        print(f"  - ({v['fecha']}) {v['situacion']} -> {v['leccion']}")
    print("=" * 40 + "\n")


# ======================================================================
# 7. Demo
# ======================================================================
if __name__ == "__main__":
    # --- Conversación A: se forman las tres memorias ---
    print(preguntar("Hola, soy Ana. Estoy aprendiendo LangGraph y uso Python 3.12 en Windows.", "conv-A"))
    # -> semántica

    print(preguntar("Prefiero respuestas de máximo 3 líneas y con el código primero.", "conv-A"))
    # -> procedural (el agente reescribe sus instrucciones)

    print(preguntar("Me sale ImportError: cannot import name 'create_agent' al correr mi agente. ¿Qué hago?", "conv-A"))
    print(preguntar("Era eso: tenía langchain 0.3 instalado. Actualicé a 1.x y ya funciona.", "conv-A"))

    # Fin de la experiencia -> se consolida como episodio
    ep = consolidar_episodio(agent, model, thread_id="conv-A", user_id="ana")
    print("\nEpisodio guardado:", ep.model_dump())

    mostrar_memorias("ana")

    # --- Conversación B: hilo NUEVO (sin historial), pero con memoria a largo plazo ---
    # Episódica: reconoce el problema parecido. Procedural: responde corto y con código primero.
    print(preguntar("Otra vez un ImportError, ahora con create_react_agent. ¿Qué reviso?", "conv-B"))

    # Semántica: recupera hechos por significado, no por clave exacta
    print(preguntar("¿Qué sabes de mi entorno de trabajo?", "conv-B"))

    # --- Otro usuario: namespaces aislados, instrucciones por defecto ---
    print(preguntar("Hola, ¿qué sabes de mí?", "conv-C", user_id="luis"))