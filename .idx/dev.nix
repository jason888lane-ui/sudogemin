{ pkgs, ... }: {
  channel = "stable-24.05";

  packages = [
    pkgs.git
    pkgs.python312
    pkgs.python312Packages.pip
    pkgs.python312Packages.virtualenv
  ];

  idx = {
    extensions = [
      "ms-python.python"
      "ms-python.vscode-pylance"
    ];

    workspace = {
      onCreate = ''
        if [ ! -d "backend/api/.venv" ]; then
          python3 -m venv backend/api/.venv
        fi
        . backend/api/.venv/bin/activate
        python -m pip install --upgrade pip
        pip install -r backend/api/requirements.txt
      '';

      onStart = ''
        cd backend/api
        . .venv/bin/activate
        uvicorn main:app --host 0.0.0.0 --port 8080 --reload
      '';
    };

    previews = {
      enable = true;
      previews = [
        {
          command = [
            "bash"
            "-lc"
            "cd backend/api && . .venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port 8080"
          ];
          manager = "web";
          port = 8080;
          name = "Sudogemin API";
        }
      ];
    };
  };
}
