#!/bin/bash
cd /mnt/c/Users/tmela/development/pokemans/pokemon-showdown

# Write commands to a temp file
cat > /tmp/ps_test_input.txt << 'EOF'
>start {"formatid":"gen9randombattle"}
>player p1 {"name":"Alice"}
>player p2 {"name":"Bob"}
EOF

# Run the simulator
timeout 5 node pokemon-showdown simulate-battle < /tmp/ps_test_input.txt
