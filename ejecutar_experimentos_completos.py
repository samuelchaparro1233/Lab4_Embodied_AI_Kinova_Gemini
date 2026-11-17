"""
Script de automatización para ejecutar los 5 Experimentos del Laboratorio IA 01B.
Ejecuta las consultas requeridas por la guía para poblar resultados/resultados.csv:
- Exp 1: Semántica con partición Desarrollo/Prueba (D/P) (3 repeticiones por escena)
- Exp 2: Cajas delimitadoras e incertidumbre (5 repeticiones en semantica1 y burger)
- Exp 3: Exactitud 3D y ablación controlada del registro (dist1 a dist5 con y sin registro)
- Exp 4: Costo del razonamiento (thinking low vs high, 5 repeticiones)
- Exp 5: Espacio de trabajo y agarre seguro/inseguro (agarre_s1..s3 e i1..i3)
"""

import os
import subprocess
import sys
import time

GRUPO = "G03"

def ejecutar_comando(cmd: list):
    res = subprocess.run([sys.executable] + cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[!] Error: {res.stderr}")
    else:
        # Extraer información del modelo y punto 3D
        lineas = res.stdout.strip().splitlines()
        modo_str = "[SIMULADO]"
        if any("gemini-robotics" in l or "gemini-2" in l for l in lineas):
            modo_str = "[GEMINI API EN VIVO]"
        elif any("manual" in l or "--respuesta" in " ".join(cmd) for l in lineas):
            modo_str = "[MANUAL/ABLACIÓN]"
            
        punto_str = ""
        for l in lineas:
            if "Punto 3D localizado" in l:
                punto_str = l.replace("[OK] ", "")
            elif "Distancia 3D" in l:
                punto_str = l.replace("[OK] ", "")
            elif "se abstuvo" in l:
                punto_str = "Abstención por seguridad (lista vacía)"
                
        escena_nombre = cmd[3] if len(cmd) > 3 else ""
        tarea_nombre = cmd[5] if len(cmd) > 5 else ""
        print(f"   {modo_str:22s} -> {escena_nombre:12s} ({tarea_nombre}): {punto_str}")


from embodied_comun import obtener_api_key

def correr_todos_los_experimentos():
    api_key = obtener_api_key()
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key

    print("=" * 75)
    if os.environ.get("GEMINI_API_KEY"):
        print("  MODO: CONECTADO EN VIVO A GOOGLE GEMINI API (gemini-robotics-er-2-preview)")
    else:
        print("  MODO: SIMULACIÓN MATEMÁTICA OFFLINE (GEMINI_API_KEY no detectada)")
    print("=" * 75)

    # -------------------------------------------------------------------------
    # EXPERIMENTO 1: Razonamiento Semántico D vs P (Tabla 6)
    # -------------------------------------------------------------------------
    print("\n>>> EXPERIMENTO 1: Razonamiento Semántico D vs P (3 repeticiones por escena)")
    escenas_exp1 = [
        ("semantica1", "propio", "la caja de hamburguesa libre y segura para tomar desde arriba"),
        ("semantica1p", "propio", "la caja de hamburguesa libre y segura para tomar desde arriba"),
        ("semantica2", "propio", "el celular que pertenece a la persona que dejó sus llaves al lado"),
        ("semantica2p", "propio", "el celular que pertenece a la persona que dejó sus llaves al lado"),
        ("semantica3", "propio", "el cuaderno que está abierto y listo para tomar apuntes"),
        ("semantica3p", "propio", "el cuaderno que está abierto y listo para tomar apuntes"),
        ("semantica4", "propio", "el esfero que está dentro del estuche"),
        ("semantica4p", "propio", "el esfero que está dentro del estuche"),
        ("burger", "propio", "la caja de hamburguesa libre"),
        ("burger", "ingenuo", "la caja"),
    ]

    for escena, tarea, objeto in escenas_exp1:
        for rep in range(1, 4):
            ejecutar_comando([
                "embodied_sin_ros.py", "consultar",
                "--escena", escena,
                "--tarea", tarea,
                "--objeto", objeto,
                "--grupo", GRUPO,
                "--thinking", "low"
            ])

    # -------------------------------------------------------------------------
    # EXPERIMENTO 2: Cajas Delimitadoras e Incertidumbre (Tabla 7)
    # -------------------------------------------------------------------------
    print("\n>>> EXPERIMENTO 2: Cajas Delimitadoras e Incertidumbre (5 repeticiones)")
    for escena in ["semantica1", "burger"]:
        for rep in range(1, 6):
            ejecutar_comando([
                "embodied_sin_ros.py", "consultar",
                "--escena", escena,
                "--tarea", "cajas",
                "--objeto", "",
                "--grupo", GRUPO,
                "--thinking", "low"
            ])

    # -------------------------------------------------------------------------
    # EXPERIMENTO 3: Exactitud 3D y Ablación del Registro (Tabla 8)
    # -------------------------------------------------------------------------
    print("\n>>> EXPERIMENTO 3: Exactitud 3D y Ablación Controlada del Registro")
    dist_configs = [
        ("dist1", '[{"point": [500, 312], "label": "termo"}, {"point": [500, 586], "label": "caja"}]'),
        ("dist2", '[{"point": [500, 312], "label": "termo"}, {"point": [500, 532], "label": "caja"}]'),
        ("dist3", '[{"point": [500, 312], "label": "termo"}, {"point": [500, 646], "label": "caja"}]'),
        ("dist4", '[{"point": [500, 104], "label": "termo"}, {"point": [500, 297], "label": "caja"}]'),
        ("dist5", '[{"point": [500, 104], "label": "termo"}, {"point": [500, 403], "label": "caja"}]'),
    ]

    for escena, resp_json in dist_configs:
        # Condición 1: CON registro (2 repeticiones)
        for _ in range(2):
            ejecutar_comando([
                "embodied_sin_ros.py", "consultar",
                "--escena", escena,
                "--tarea", "puntos",
                "--objeto", "el termo y la caja",
                "--grupo", GRUPO,
                "--respuesta", resp_json
            ])
        # Condición 2: SIN registro (ablación con respuesta fija reutilizada)
        for _ in range(2):
            ejecutar_comando([
                "embodied_sin_ros.py", "consultar",
                "--escena", escena,
                "--tarea", "puntos",
                "--objeto", "el termo y la caja",
                "--grupo", GRUPO,
                "--sin-registro",
                "--respuesta", resp_json
            ])

    # -------------------------------------------------------------------------
    # EXPERIMENTO 4: Costo del Razonamiento (Tabla 9)
    # -------------------------------------------------------------------------
    print("\n>>> EXPERIMENTO 4: Costo del Razonamiento (low vs high)")
    for nivel in ["low", "high"]:
        for rep in range(1, 6):
            ejecutar_comando([
                "embodied_sin_ros.py", "consultar",
                "--escena", "semantica2p",
                "--tarea", "propio",
                "--objeto", "el celular que pertenece a la persona que dejó sus llaves al lado",
                "--grupo", GRUPO,
                "--thinking", nivel
            ])

    # -------------------------------------------------------------------------
    # EXPERIMENTO 5: Espacio de Trabajo, Agarre y Seguridad (Tabla 10)
    # -------------------------------------------------------------------------
    print("\n>>> EXPERIMENTO 5: Seguridad de Agarre (3 seguras, 3 inseguras)")
    # Seguras (s1..s3)
    for s_idx in [1, 2, 3]:
        ejecutar_comando([
            "embodied_sin_ros.py", "consultar",
            "--escena", f"agarre_s{s_idx}",
            "--tarea", "agarre",
            "--objeto", "la caja de hamburguesa",
            "--grupo", GRUPO
        ])
    # Inseguras (i1..i3)
    for i_idx in [1, 2, 3]:
        ejecutar_comando([
            "embodied_sin_ros.py", "consultar",
            "--escena", f"agarre_i{i_idx}",
            "--tarea", "agarre",
            "--objeto", "la caja de hamburguesa",
            "--grupo", GRUPO
        ])

    print("\n" + "=" * 70)
    print("[OK] ¡Todos los experimentos se han ejecutado y registrado en resultados/resultados.csv!")
    print("=" * 70)


if __name__ == "__main__":
    correr_todos_los_experimentos()
