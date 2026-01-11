# Perfil: sin coords visibles (seguro)
# - deshabilita coords OCR para evitar falsos positivos
# - usa modo StepNavigator (no requiere coords absolutas)

$env:COORDS_PROVIDER = 'disabled'
$env:CAVEBOT_MODE = 'steps'

# Opcional: reducir ruido
# $env:BOT_DEBUG = '0'

poetry run python run_bot_ui.py
