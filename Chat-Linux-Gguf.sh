#!/bin/bash

# Initialize logging
log_message() {
    local message="$1"
    local level="${2:-INFO}"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $level: $message"
}

# Set terminal title
echo -ne "\033]0;Chat-Linux-Gguf\007"

# Set terminal size to 107x24 (for llama.cpp output)
if [[ -t 1 ]]; then
    log_message "Setting terminal size to 107x24"
    echo -ne "\033[8;24;107t"
    sleep 0.5  # Allow time for resize to complete
fi

# Determine script directory and change to it
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR" || {
    log_message "Error: Failed to change to script directory." "ERROR"
    sleep 3
    exit 1
}
log_message "Changed to script directory: $SCRIPT_DIR"
sleep 1

# Check for required files
for file in launcher.py installer.py validater.py; do
    if [ ! -f "$file" ]; then
        log_message "Error: Required file $file not found in $SCRIPT_DIR" "ERROR"
        sleep 3
        exit 1
    fi
done
log_message "Required files found"
sleep 1

# Separator functions for 107 width terminal
display_separator_thick() {
    echo "===========================================================================================================" | cut -c 1-107
}

display_separator_thin() {
    echo "-----------------------------------------------------------------------------------------------------------" | cut -c 1-107
}

# Main menu (107-width)
main_menu() {
    clear
    display_separator_thick
    echo "    Chat-Linux-Gguf: Bash Menu" | awk '{printf "%-107s\n", $0}'
    display_separator_thick
    echo ""
    echo ""
    echo ""
    echo ""
    echo ""
    echo "" 
    echo "    1. Run Main Program" | awk '{printf "%-107s\n", $0}'
    echo ""
    echo "    2. Run Installation" | awk '{printf "%-107s\n", $0}'
    echo ""
    echo "    3. Run Validation" | awk '{printf "%-107s\n", $0}'
    echo ""
    echo ""
    echo ""
    echo ""
    echo ""
    echo ""
    display_separator_thick
    read -p "Selection; Menu Options = 1-3, Exit Bash = X: " choice
    choice=${choice//[[:space:]]/} # Trim whitespace
    process_choice
}

# Choice processing with retry limit
MAX_RETRIES=3
retry_count=0
process_choice() {
    case "$choice" in
        1)
            run_main_program
            ;;
        2)
            run_installation
            ;;
        3)
            run_validation
            ;;
        X|x)
            log_message "Closing Chat-Linux-Gguf..."
            sleep 1
            exit 0
            ;;
        *)
            ((retry_count++))
            log_message "Invalid selection. Attempt $retry_count of $MAX_RETRIES." "WARNING"
            sleep 1
            if [ "$retry_count" -ge "$MAX_RETRIES" ]; then
                log_message "Maximum retries reached. Exiting." "ERROR"
                sleep 3
                exit 1
            fi
            retry_count=0  # Reset counter for next menu call
            main_menu
            ;;
    esac
}

# Check if running interactively
pause_if_interactive() {
    if [[ -t 0 ]]; then
        read -p "Press Enter to continue..."
    fi
}

# Option handlers
run_main_program() {
    clear
    display_separator_thick
    echo "    Chat-Linux-Gguf: Launcher" | awk '{printf "%-107s\n", $0}'
    display_separator_thick
    echo ""
    echo "Checking environment..." | awk '{printf "%-107s\n", $0}'
    
    # Check virtual environment
    if [ ! -f ".venv/bin/python" ]; then
        log_message "Error: Virtual environment missing. Please run installation first." "ERROR"
        sleep 3
        pause_if_interactive
        main_menu
        return
    fi
    
    # Check configuration
    if [ ! -f "data/persistent.json" ]; then
        log_message "Error: Configuration file missing. Please run installation first." "ERROR"
        sleep 3
        pause_if_interactive
        main_menu
        return
    fi
    
    echo "Starting Chat-Linux-Gguf..." | awk '{printf "%-107s\n", $0}'
    sleep 1
    
    # Activate virtual environment
    source .venv/bin/activate
    log_message "Activated: .venv"
    sleep 1
    
    # Set PYTHONUNBUFFERED for real time output
    export PYTHONUNBUFFERED=1
    
    # Run the launcher script
    python3 -u launcher.py
    local exit_code=$?
    
    # Deactivate virtual environment
    deactivate
    log_message "Deactivated: .venv"
    sleep 1
    unset PYTHONUNBUFFERED
    
    if [ $exit_code -ne 0 ]; then
        log_message "Program exited with error (code: $exit_code)" "ERROR"
        sleep 3
    fi
    
    pause_if_interactive
    main_menu
}

run_installation() {
    clear
    display_separator_thick
    echo "    Chat-Linux-Gguf: Installer" | awk '{printf "%-107s\n", $0}'
    display_separator_thick
    echo ""
    
    # Remove the confirmation prompt entirely
    echo "Starting installer..." | awk '{printf "%-107s\n", $0}'
    sleep 1
    
    # Run the installer script directly
    python3 installer.py
    local exit_code=$?
    
    if [ $exit_code -ne 0 ]; then
        log_message "Installation failed (code: $exit_code)" "ERROR"
        sleep 3
    else
        log_message "Installation completed" "INFO"
        sleep 1
    fi
    
    # Ensure venv is deactivated
    deactivate 2>/dev/null || true
    log_message "Virtual environment status reset"
    sleep 1
    
    pause_if_interactive
    main_menu
}

run_validation() {
    clear
    display_separator_thick
    echo "    Chat-Linux-Gguf: Validation" | awk '{printf "%-107s\n", $0}'
    display_separator_thick
    echo ""
    
    # Check virtual environment
    if [ ! -f ".venv/bin/python" ]; then
        log_message "Error: Virtual environment not found" "ERROR"
        sleep 3
        pause_if_interactive
        main_menu
        return
    fi
    
    echo "Running validation checks..." | awk '{printf "%-107s\n", $0}'
    sleep 1
    
    # Activate virtual environment
    source .venv/bin/activate
    log_message "Activated: .venv"
    sleep 1
    
    # Run the validation script
    python3 validater.py
    local exit_code=$?
    
    # Deactivate virtual environment
    deactivate
    log_message "Deactivated: .venv"
    sleep 1
    
    if [ $exit_code -ne 0 ]; then
        log_message "Validation failed (code: $exit_code)" "ERROR"
        sleep 3
    else
        log_message "Validation successful" "INFO"
        sleep 1
    fi
    
    pause_if_interactive
    main_menu
}

# Start the script
log_message "Starting Chat-Linux-Gguf"
sleep 1
main_menu