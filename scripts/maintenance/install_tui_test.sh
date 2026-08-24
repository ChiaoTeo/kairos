#!/bin/sh
set -eu

TUI_TEST_VERSION="0.1.0-beta.2"
TUI_TEST_INSTALL_DIR="${TUI_TEST_INSTALL_DIR:-.agent-work/textual-agent-workflow/bin}"
export TUI_TEST_VERSION TUI_TEST_INSTALL_DIR

curl --proto '=https' --tlsv1.2 -LsSf \
  "https://raw.githubusercontent.com/microsoft/tui-test/${TUI_TEST_VERSION}/install/install.sh" \
  | sh

"${TUI_TEST_INSTALL_DIR}/tui-test" --version \
  | grep -F "tui-test ${TUI_TEST_VERSION}"
