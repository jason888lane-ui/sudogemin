/* Public browser configuration only. Never place client secrets or API secrets here. */
window.SUDOGEMIN_CONFIG = {
  apiBaseUrl: "",       // Empty means same-origin API proxy.
  oidcIssuer: "",       // Exact HTTPS issuer URL from your OIDC provider.
  oidcClientId: "",     // Public SPA client ID; enable Authorization Code + PKCE.
  oidcRedirectUri: window.location.origin + window.location.pathname,
  oidcScopes: "openid profile email",
  oidcAudience: ""      // Optional provider-specific audience/scope configuration.
};