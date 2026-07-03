# La Fábrica de Software — Guía para principiantes

*English version: [BEGINNERS-GUIDE.md](BEGINNERS-GUIDE.md)*

Esta guía está escrita para personas que **no son programadoras profesionales**.
Si usted puede instalar un programa y copiar y pegar texto en una ventana, puede
usar la Fábrica de Software.

---

## 1. ¿Qué es esto, en palabras sencillas?

Imagine un pequeño taller con asistentes incansables:

- Un **planificador** lee su petición y la divide en trabajos pequeños.
- Varios **constructores** hacen esos trabajos al mismo tiempo, cada uno en su
  propia copia, para no estorbarse entre sí.
- Un **inspector** revisa la calidad del trabajo (¿funciona? ¿hay errores?).
- Un **guardia de seguridad** revisa que no se haya colado nada peligroso.
- Un **técnico de reparaciones** corrige problemas automáticamente cuando una
  revisión falla.
- Un **tablero de control** en su pantalla le muestra todo lo que hicieron hoy.

Esos asistentes son agentes de inteligencia artificial (IA). Usted escribe **qué**
quiere, en lenguaje normal — "agrega una página que muestre un mensaje de
bienvenida" — y la fábrica se encarga del **cómo**.

Hay exactamente una cosa que la fábrica nunca puede hacer sola: **publicar
(desplegar) la aplicación**. Un ser humano — usted — siempre debe presionar el
botón de aprobación.

## 2. Lo que necesita antes de empezar (se hace una sola vez)

Necesita dos programas y una llave. Los programas son gratuitos; la llave viene con
una cuenta en un proveedor de IA — usted elige cuál.

### 2.1 Instalar Python (el motor)

1. Vaya a https://www.python.org/downloads/ y haga clic en el botón amarillo de
   descarga.
2. Ejecute el instalador. **Importante:** en la primera pantalla, marque la casilla
   "**Add Python to PATH**" antes de hacer clic en Install.

### 2.2 Instalar Git (el archivero)

1. Vaya a https://git-scm.com/downloads y descargue la versión para Windows.
2. Ejecute el instalador y haga clic en "Next" en todas las pantallas. Las opciones
   predeterminadas están bien.

### 2.3 Obtener el cerebro de la fábrica: una llave (API key) de un proveedor de IA

Los asistentes de la fábrica funcionan con un proveedor de IA. Actualmente funciona
con **dos proveedores — elija el que prefiera** (solo necesita uno):

| Proveedor | Dónde obtener la llave | La llave se ve así |
|---|---|---|
| **Cursor** | https://cursor.com → Dashboard → Integrations → API key | `cursor_abc123...` |
| **Anthropic (Claude)** | https://console.anthropic.com → API keys → Create key | `sk-ant-abc123...` |

Copie la llave en un lugar seguro. Trátela como una contraseña: no la comparta ni
la publique en ningún lado. (La fábrica está construida para poder agregar otros
proveedores con el tiempo — si su institución usa uno distinto, consulte con un
colega técnico.)

### 2.4 Abrir una terminal

Todo lo que sigue ocurre en una **terminal**: una ventana donde se escriben
comandos. En Windows: presione la tecla **Inicio**, escriba `powershell` y presione
Enter. Se abre una ventana azul. Eso es todo. Usted copia un comando de esta guía,
lo pega ahí (clic derecho pega) y presiona Enter.

## 3. Obtener su propia copia de la fábrica

1. Abra https://github.com/chhdeza/My-SW-Factory en su navegador.
2. Haga clic en el botón verde **"Use this template"** → **"Create a new
   repository"**. (Necesita una cuenta gratuita de GitHub: https://github.com/signup.)
3. Póngale un nombre, por ejemplo `mi-primera-app`, y haga clic en
   **Create repository**.
4. Ahora cópiela a su computadora. En la terminal, pegue (reemplace SU-USUARIO):

```powershell
cd $HOME\Documents
git clone https://github.com/SU-USUARIO/mi-primera-app
cd mi-primera-app
```

## 4. Instalar la fábrica (5 minutos, una vez por proyecto)

Pegue estos tres comandos uno por uno, presionando Enter después de cada uno.
En el tercer comando, use el nombre del proveedor que eligió en el paso 2.3 —
`cursor` o `claude`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,cursor]"     # o bien:  pip install -e ".[dev,claude]"
```

> Si el segundo comando se queja de "execution policy", ejecute esto una vez y
> vuelva a intentar:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

¿Qué hizo eso? Creó una caja de herramientas privada para este proyecto (`venv`),
la activó e instaló la fábrica adentro.

## 5. Despertar la fábrica

```powershell
factory init
```

La fábrica le hará algunas preguntas. **Presione Enter para aceptar todas las
opciones predeterminadas**, excepto:

- Cuando pregunte por el **proveedor de agentes predeterminado** ("default agent
  provider"), escriba el que eligió en el paso 2.3: `cursor` o `claude`.
- Cuando pida la llave correspondiente — **CURSOR_API_KEY** o
  **ANTHROPIC_API_KEY** — pegue la llave del paso 2.3. (Deje la otra vacía
  presionando Enter.)
- Cuando pregunte *"Install the open-source gate tools now?"* responda **y** (sí).
  Esto instala las herramientas de inspección gratuitas para que las revisiones de
  calidad y seguridad funcionen.

Listo. La fábrica está despierta.

## 6. Crear su primera aplicación

La fábrica necesita una pequeña semilla para crecer. Plantemos una: una aplicación
web diminuta.

Cree un archivo llamado `app.py` en la carpeta del proyecto (puede usar el Bloc de
notas: escriba `notepad app.py` en la terminal) con este contenido:

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root():
    return {"message": "¡Hola desde nuestra universidad!"}
```

Y un archivo llamado `test_app.py`:

```python
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_root():
    assert client.get("/").json() == {"message": "¡Hola desde nuestra universidad!"}
```

Guarde ambos y avísele al archivero:

```powershell
git add app.py test_app.py
git commit -m "feat: mi primera aplicacion"
```

## 7. Pedirle a la fábrica una nueva funcionalidad

Este es el momento mágico. Escriba:

```powershell
factory run "Agrega una pagina en /bienvenida que devuelva un mensaje de bienvenida con el nombre de la universidad, e incluye una prueba"
```

Espere de 2 a 10 minutos y observe. La fábrica va a:

1. **Planificar** — dividir su petición en trabajos.
2. **Construir** — dos o más constructores de IA escriben el código en paralelo.
3. **Inspeccionar** — ejecutar las revisiones de calidad (estilo del código,
   pruebas) y de seguridad.
4. **Reparar** — si una revisión falla, el agente de reparación intenta
   corregirla por sí solo.
5. **Entregar** — si todo pasa y el cambio es de bajo riesgo, integra la nueva
   funcionalidad a su aplicación automáticamente.

Al terminar verá un mensaje como `task task-xxxx: merged ... (low risk)`.

**Vea su nueva funcionalidad en acción:**

```powershell
pip install uvicorn
uvicorn app:app
```

Abra http://127.0.0.1:8000/bienvenida en su navegador. Ahí está su página —
escrita, probada y revisada sin que usted escribiera una sola línea de código.
Presione `Ctrl+C` en la terminal para detenerla.

## 8. Observar la fábrica trabajar: el tablero de control

```powershell
factory dashboard
```

Abra http://localhost:8700 en su navegador. Verá:

- **Los números de hoy**: cuántos agentes de IA trabajaron, cuánto costó
  (estimado), cuántas reparaciones y entregas hubo.
- **Tareas**: todo lo que usted ha pedido y su estado actual.
- **Ejecuciones de agentes**: cada trabajador de IA y lo que hizo.
- **Trazas**: haga clic en una para leer la conversación completa con la IA
  (las contraseñas y llaves se tachan automáticamente).
- **Deploy (publicación)**: el botón de aprobación — nada se publica sin su clic.

Presione `Ctrl+C` en la terminal para detener el tablero.

## 9. Pedir más funcionalidades — la rutina diaria

De aquí en adelante, su flujo de trabajo es solo este ciclo:

```powershell
factory run "describa lo que quiere, en lenguaje normal"
```

Consejos para hacer buenas peticiones:

- **Sea específico.** "Agrega una página en /horario que muestre una lista de
  nombres de cursos" funciona mejor que "mejora la aplicación".
- **Una funcionalidad a la vez.** Las peticiones pequeñas tienen más éxito y
  cuestan menos.
- **Siempre mencione pruebas.** Termine con "e incluye una prueba" — así la
  fábrica demuestra su propio trabajo.

Si la fábrica dice que un cambio quedó **"held for human review"** (retenido para
revisión humana), eso es una medida de seguridad, no un error: el cambio parecía
riesgoso (muy grande, tocó archivos delicados) y está esperando que un humano lo
revise.

## 10. Si algo sale mal

| Lo que ve | Qué significa | Qué hacer |
|---|---|---|
| `CURSOR_API_KEY is not set` / `ANTHROPIC_API_KEY is not set` | La fábrica no encuentra la llave de su proveedor | Ejecute `factory init` de nuevo y pegue la llave |
| `gates failed after self-heal` | Las revisiones fallaron y la auto-reparación no pudo | Intente de nuevo con una descripción más sencilla |
| `budget exceeded` | La tarea llegó a su límite de gasto (una protección) | Suba el límite en `factory.yaml` o simplifique la petición |
| La terminal dice que `factory` no se reconoce | La caja de herramientas no está activa | Ejecute primero `.venv\Scripts\Activate.ps1` |

## 11. Cuánto cuesta

La fábrica en sí es gratuita y de código abierto. El único costo es el uso de IA
que le cobra su proveedor (Cursor o Anthropic). La fábrica tiene límites de gasto
integrados (aproximadamente
$5 por tarea y $25 por día de forma predeterminada) y el tablero muestra el gasto
estimado, así que no hay sorpresas. Una funcionalidad pequeña normalmente cuesta
entre unos centavos y unas decenas de centavos de dólar.

---

*¿Preguntas o ideas? Abra un "Issue" en la página de GitHub del proyecto — ese es
el buzón público de sugerencias.*
