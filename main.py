from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
import os
from langchain.chat_models import init_chat_model
from langchain_tavily import TavilySearch
from langgraph.prebuilt import ToolNode, tools_condition
from datetime import datetime,timedelta
from langchain_core.tools import tool
from fastapi import FastAPI, Depends, HTTPException, Header, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from jose import JWTError, jwt
import secrets
from fastapi.responses import StreamingResponse
import asyncio
import json

SECRET_KEY = secrets.token_urlsafe(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
    
def require_token(authorization: str = Header(...)):
    token = authorization.split(" ")[1] if " " in authorization else authorization
    user = verify_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user

os.environ["GOOGLE_API_KEY"] = "AIzaSyCZ1QMjBF7lHmg1adbO4rMUvqwmRVpfTWU"
os.environ["TAVILY_API_KEY"] = "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"

llm = init_chat_model("google_genai:gemini-2.0-flash")

@tool
def get_current_time() -> str:
    """Returns the current time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

tool = TavilySearch(max_results=2)
tools = [tool,get_current_time]
llm_with_tools = llm.bind_tools(tools)

class State(TypedDict):
    messages: Annotated[list, add_messages]

graph_builder = StateGraph(State)

def chatbot(state: State):
    # return {"messages": [llm.invoke(state["messages"])]}
    return {"messages": [llm_with_tools.invoke(state["messages"])]}

graph_builder.add_node("chatbot", chatbot)
graph_builder.add_edge(START, "chatbot")

tool_node = ToolNode(tools=tools)
graph_builder.add_node("tools", tool_node)

graph_builder.add_conditional_edges("chatbot", tools_condition)
graph_builder.add_edge("tools", "chatbot")
# graph_builder.add_edge(START, "chatbot")

graph = graph_builder.compile()

app = FastAPI()

# Enable CORS (helpful for Postman & frontend testing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class MessageRequest(BaseModel):
    message: str

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/login")
def login(request: LoginRequest):
    if request.username == "admin" and request.password == "admin":
        token = create_access_token({"sub": request.username})
        return {"access_token": token}
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.post("/chat")
async def chat_stream(request: MessageRequest, user: dict = Depends(require_token)):
    user_msg = {"role": "user", "content": request.message}

    async def event_gen():
        async for event in graph.astream({"messages": [user_msg]}):
            for value in event.values():
                final_msg = value["messages"][-1].content
                for char in final_msg:
                    yield f"data: {json.dumps({'response': char})}\n\n"
                    await asyncio.sleep(0.01)  # simulate delay for stream effect

    return StreamingResponse(event_gen(), media_type="text/event-stream")

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket, token: str = None):
    # Accept WebSocket connection
    await websocket.accept()

    # Authenticate JWT token
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user = verify_token(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        while True:
            data = await websocket.receive_json()
            user_msg = {"role": "user", "content": data.get("message", "")}

            async for event in graph.astream({"messages": [user_msg]}):
                for value in event.values():
                    final_msg = value["messages"][-1].content
                    for char in final_msg:
                        await websocket.send_json({"response": char})
                        await asyncio.sleep(0.01)  # Simulate stream

    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        print("WebSocket error:", e)
        await websocket.close()






