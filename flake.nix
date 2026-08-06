{
  description = "dynaconf-ssm-tenant-loader — multi-tenant AWS SSM Parameter Store loader for Dynaconf";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.uv
            pkgs.ruff
          ];

          env = {
            # Use the Nix-provided interpreter instead of letting uv
            # download its own managed Python.
            UV_PYTHON = "${python}/bin/python";
            UV_PYTHON_DOWNLOADS = "never";
          };

          shellHook = ''
            uv sync --quiet
            echo "Run tests with: uv run pytest"
          '';
        };

        # `nix flake check` runs the test suite in a sandboxed-ish
        # manner (network is available to uv here because this is a
        # devshell-style check, not a pure build).
        apps.test = {
          type = "app";
          program = toString (
            pkgs.writeShellScript "run-tests" ''
              export UV_PYTHON="${python}/bin/python"
              export UV_PYTHON_DOWNLOADS=never
              ${pkgs.uv}/bin/uv sync --quiet
              exec ${pkgs.uv}/bin/uv run pytest "$@"
            ''
          );
        };
      }
    );
}
