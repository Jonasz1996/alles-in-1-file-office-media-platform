{{- define "platform.host" -}}
{{ index .root.Values.hosts .name }}.{{ .root.Values.domain }}
{{- end -}}

{{- define "platform.middlewares" -}}
platform-security-headers@kubernetescrd,platform-rate-limit@kubernetescrd
{{- if .admin }}{{ if .root.Values.security.adminAllowCidrs }},platform-admin-allowlist@kubernetescrd{{ end }}{{ end }}
{{- end -}}
