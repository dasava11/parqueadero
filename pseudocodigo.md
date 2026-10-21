FUNCIÓN EJECUTAR_VEHÍCULO(vehículo):

    esperar un intervalo aleatorio inicial

    registrar que el vehículo intenta ingresar

    agregar vehículo a la cola de espera

    actualizar estado a ESPERANDO_ACCESO

    SOLICITAR un permiso al SEMÁFORO
        si no hay permisos, esperar
        cuando se libere uno, continuar

    adquirir LOCK
        retirar vehículo de la cola de espera
        registrar vehículo como ESTACIONADO
        asignar un espacio disponible
        actualizar contadores
    liberar LOCK

    notificar evento de ingreso a la interfaz

    permanecer estacionado durante un tiempo aleatorio
        o hasta recibir una solicitud de salida

    adquirir LOCK
        marcar vehículo como SALIENDO
        liberar el espacio en los datos compartidos
        actualizar contadores
    liberar LOCK

    devolver el permiso al SEMÁFORO

    notificar evento de salida a la interfaz

    finalizar el hilo

\*\*responsabilidades del codigo diferenciadas:

Crear vehículos > Genera identificadores y solicita la creación de nuevos vehículos
Ejecutar vehículos > Administra el ciclo de vida de un vehículo dentro de su hilo
Gestionar el parqueadero > Controla espacios, permisos, ocupación y cola
Gestionar la interfaz > Dibuja los vehículos, indicadores, botones y estadísticas
Procesar métricas > Calcula tiempos de espera y ocupación
Registrar evento > Guarda ingresos, salidas y estados para su análisis

¿Qué responsabilidades tendrá VehicleWorker?
El trabajador debe:

1. Esperar el tiempo de llegada definido para el vehículo.
2. Solicitar acceso al parqueadero.
3. Medir cuánto tiempo espera para conseguir una plaza.
4. Permanecer estacionado durante el tiempo previsto.
5. Atender una solicitud de salida anticipada.
6. Liberar el espacio al terminar.
7. Comunicar los eventos a los demás componentes mediante queue.Queue().

core/parking.py
cinco responsabilidades:

1. Representar los 20 espacios.
2. Saber qué vehículo está en cada espacio y cuáles están esperando.
3. Controlar la capacidad mediante un semáforo.
4. Proteger los datos compartidos mediante un Lock.
5. Ofrecer operaciones para ocupar y liberar espacios.
