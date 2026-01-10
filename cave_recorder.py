import os
import pyperclip
import keyboard
import threading
import time

class TibiaCaveRecorder:
    def __init__(self, filename="cave_script.txt"):
        self.filename = filename
        self.recording = False
        self.paused = False
        self.hotkey_node = 'f12'
        self.hotkey_stand = 'f11'

        # Crear archivo vacío
        with open(self.filename, 'w') as f:
            f.write("label start\n")

        print(f"📁 Script se guardará en: {os.path.abspath(self.filename)}")
        print("⌨️  Comandos disponibles durante la grabación:")
        print("   - Escribe 'label NOMBRE' para añadir una etiqueta")
        print("   - Escribe 'action NOMBRE' para añadir una acción")
        print("   - Escribe 'rope', 'ladder', 'shovel' para tile interactivo")
        print("   - Pulsa F12 para guardar nodo (node)")
        print("   - Pulsa F11 para guardar stand")
        print("   - Escribe 'stop' para terminar\n")

    def _parse_position_from_clipboard(self):
        """Extrae (x, y, z) del portapapeles en varios formatos."""
        try:
            text = pyperclip.paste().strip()
            if not text:
                return None
            # Limpiar y extraer números
            clean = ''.join(c if c.isdigit() or c in ' ,-_' else ' ' for c in text)
            parts = [s for s in clean.split() if s.lstrip('-').isdigit()]
            if len(parts) >= 3:
                return tuple(int(p) for p in parts[:3])
        except Exception as e:
            print(f"⚠️ Error al leer portapapeles: {e}")
        return None

    def _append_line(self, line):
        with open(self.filename, 'a') as f:
            f.write(line + "\n")
        print(f"✅ {line}")

    def _on_node(self):
        if self.paused:
            return
        pos = self._parse_position_from_clipboard()
        if pos:
            self._append_line(f"node {pos}")
        else:
            print("❌ No hay coordenadas válidas en el portapapeles.")

    def _on_stand(self):
        if self.paused:
            return
        pos = self._parse_position_from_clipboard()
        if pos:
            self._append_line(f"stand {pos}")
        else:
            print("❌ No hay coordenadas válidas en el portapapeles.")

    def _add_special_command(self, cmd):
        """Añade rope, ladder, shovel, etc., usando la posición actual del portapapeles."""
        pos = self._parse_position_from_clipboard()
        if pos:
            self._append_line(f"{cmd} {pos}")
        else:
            print(f"❌ Necesitas copiar coordenadas para usar '{cmd}'.")

    def _listen_console(self):
        """Escucha comandos desde la consola."""
        while self.recording:
            try:
                user_input = input().strip()
                if not user_input:
                    continue

                if user_input.lower() == 'stop':
                    self.recording = False
                    break

                elif user_input.lower().startswith('label '):
                    label_name = user_input[6:].strip()
                    self._append_line(f"label {label_name}")

                elif user_input.lower().startswith('action '):
                    action_name = user_input[7:].strip()
                    self._append_line(f"action {action_name}")

                elif user_input.lower() in ('rope', 'ladder', 'shovel'):
                    self._add_special_command(user_input.lower())

                elif user_input.lower() == 'pause':
                    self.paused = True
                    print("⏸️ Grabación pausada.")
                elif user_input.lower() == 'resume':
                    self.paused = False
                    print("▶️ Grabación reanudada.")

                else:
                    print("❓ Comando no reconocido. Usa: label X, action Y, rope, ladder, stop, pause, resume")

            except EOFError:
                break

    def start(self):
        """Inicia la grabación."""
        self.recording = True

        # Registrar hotkeys
        keyboard.add_hotkey(self.hotkey_node, self._on_node)
        keyboard.add_hotkey(self.hotkey_stand, self._on_stand)

        print("🟢 ¡Grabación iniciada!")
        print("   Copia coordenadas (ej: '32681 31686 6') y pulsa F12/F11, o escribe comandos.")

        # Hilo para escuchar consola
        console_thread = threading.Thread(target=self._listen_console, daemon=True)
        console_thread.start()

        # Esperar a que termine
        try:
            while self.recording:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass

        # Asegurar cierre limpio
        self._append_line("action end")
        print("\n⏹️ Grabación finalizada. ¡Listo para usar en tu cavebot!")