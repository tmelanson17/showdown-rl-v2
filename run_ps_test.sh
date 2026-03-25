#!/bin/bash
# Test script for Pokemon Showdown simulator

cd /mnt/c/Users/tmela/development/pokemans/pokemon-showdown

# Create input file
cat > /tmp/ps_test_input.txt << 'EOF'
>start {"formatid":"gen9randombattle"}
>player p1 {"name":"Alice"}
>player p2 {"name":"Bob"}
EOF

echo "Input commands:"
cat /tmp/ps_test_input.txt

echo ""
echo "Running simulator with --skip-build..."

# Run with timeout and save output, add --skip-build to avoid rebuild check
timeout 8 node pokemon-showdown --skip-build simulate-battle < /tmp/ps_test_input.txt 2>&1
EXIT_CODE=$?

echo ""
echo "Exit code: $EXIT_CODE"
