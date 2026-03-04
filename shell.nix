{
  pkgs ? import <nixpkgs> { },
}:

pkgs.mkShell {
  nativeBuildInputs = [
    # Runtime & Builder
    pkgs.python312
    pkgs.uv

    # LSP
    pkgs.nil # Nix
    pkgs.ruff # Python
  ];
}
