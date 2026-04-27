#!/bin/sh

# Shutdown handler. Triggered when the script receives a signal from Docker
shutdown() {
    echo "[WRAPPER] Received signal from Docker. Initiating cascading shutdown for bot (PID $BOT_PID)..."

    # Step 1: Send SIGINT (Ctrl+C equivalent) - C++ daemons usually listen to this
    echo "[WRAPPER] -> Step 1: Sending SIGINT..."
    kill -INT "$BOT_PID" 2>/dev/null

    # Give it 4 seconds to react (8 checks, 0.5s each)
    for i in 1 2 3 4 5 6 7 8; do
        kill -0 "$BOT_PID" 2>/dev/null || break
        sleep 0.5
    done

    # Step 2: If the bot is still alive, try SIGTERM
    if kill -0 "$BOT_PID" 2>/dev/null; then
        echo "[WRAPPER] -> Step 2: Process is still alive. Sending SIGTERM..."
        kill -TERM "$BOT_PID" 2>/dev/null

        for i in 1 2 3 4 5 6 7 8; do
            kill -0 "$BOT_PID" 2>/dev/null || break
            sleep 0.5
        done
    fi

    # Step 3: If it still refuses to die, force a core dump / hard quit
    if kill -0 "$BOT_PID" 2>/dev/null; then
        echo "[WRAPPER] -> Step 3: Process is ignoring signals. Sending SIGQUIT..."
        kill -QUIT "$BOT_PID" 2>/dev/null
        sleep 2
    fi

    # Wait for the actual process to exit
    wait "$BOT_PID" 2>/dev/null
    echo "[WRAPPER] telegram-bot-api successfully terminated!"
    exit 0
}

# Trap the TERM (Docker default) and INT signals
trap shutdown TERM INT

# Start the bot in the BACKGROUND (the ampersand at the end is required!)
/usr/local/bin/telegram-bot-api \
    --verbosity=8 \
    --local \
    --http-port=8081 \
    --dir=/var/lib/telegram-bot-api \
    --api-id="${TELEGRAM_API_ID}" \
    --api-hash="${TELEGRAM_API_HASH}" &

# Save the background process PID
BOT_PID=$!

echo "[WRAPPER] telegram-bot-api started in background. PID: $BOT_PID"

# Block the script using wait.
# If a signal arrives from Docker, wait is immediately interrupted and the shutdown function is called.
wait "$BOT_PID"
