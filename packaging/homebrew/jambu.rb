class Jambu < Formula
  include Language::Python::Virtualenv

  desc "AI-employee web audit & autonomous research engine"
  homepage "https://github.com/pmaero-byte/jambubrowser"
  # Per release: update url + sha256 from the PyPI sdist, then
  # `brew audit --strict --new --online` and `brew test` in the tap.
  url "https://files.pythonhosted.org/packages/source/j/jambubrowser/jambubrowser-3.3.0.tar.gz"
  sha256 "REPLACE_WITH_SDIST_SHA256"
  license "MIT"

  depends_on "python@3.12"

  resource "pip" do
    url "https://files.pythonhosted.org/packages/e8/b6/33b6d3efbac8d7004bea19f1d7fefc1a929dd7e9a6a472d352c7b96fb46/pip-25.0.1.tar.gz"
    sha256 "2b2b651716142cebbdcff2df4b93cd5d1d09f41b0c1709ec8b9a63dcd1f53ee"
  end

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      The browser driver and LLM are environment steps:
        #{bin}/jambu health          # check the engine
        python3 -m playwright install chromium
    EOS
  end

  test do
    assert_match "Usage", shell_output("#{bin}/jambu --help")
  end
end
