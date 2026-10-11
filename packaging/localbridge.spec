# DRAFT: not built or install-tested yet.
Name:           localbridge
Version:        0.1.0
Release:        1%{?dist}
Summary:        Private local-network file transfer between Android and Fedora
License:        GPL-3.0-or-later
URL:            https://example.invalid/localbridge
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  python3-devel pyproject-rpm-macros
Requires:       python3-gobject gtk4 libadwaita python3-fastapi python3-uvicorn python3-cryptography python3-qrcode

%description
LocalBridge receives files from the LocalBridge Android app over an encrypted, certificate-pinned
local connection. No cloud, no account, no background service.

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
install -Dm644 ../packaging/io.github.localbridge.LocalBridge.desktop \
  %{buildroot}%{_datadir}/applications/io.github.localbridge.LocalBridge.desktop

%files
%license LICENSE
%{python3_sitelib}/localbridge*
%{_bindir}/localbridge
%{_datadir}/applications/io.github.localbridge.LocalBridge.desktop
