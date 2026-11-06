# The CLI's default is the system engine's socket; the engine here is the user's own.
if not set -q DOCKER_HOST; and set -q XDG_RUNTIME_DIR
    set -gx DOCKER_HOST "unix://$XDG_RUNTIME_DIR/docker.sock"
end
