{{/*
Expand the name of the chart.
*/}}
{{- define "securo.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "securo.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "securo.labels" -}}
helm.sh/chart: {{ include "securo.chart" . }}
app.kubernetes.io/name: {{ include "securo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "securo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "securo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "securo.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "securo.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "securo.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Secret that holds the plugin runner token and the egress secret. Either the one
the chart creates or the user's existingSecret.
*/}}
{{- define "securo.pluginsSecretName" -}}
{{- if .Values.plugins.runner.existingSecret }}
{{- .Values.plugins.runner.existingSecret }}
{{- else }}
{{- printf "%s-plugins" (include "securo.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Image for the plugin runner and the egress proxy: the backend image unless the
user pointed them somewhere else.
*/}}
{{- define "securo.pluginsImage" -}}
{{- $repository := .Values.plugins.runner.image.repository | default .Values.backend.image.repository }}
{{- $tag := .Values.plugins.runner.image.tag | default .Values.backend.image.tag }}
{{- printf "%s:%s" $repository $tag }}
{{- end }}

{{/*
Compute the frontend URL based on global.domain and global.tls
*/}}
{{- define "securo.frontendUrl" -}}
{{- if .Values.global.tls -}}
https://{{ .Values.global.domain }}
{{- else -}}
http://{{ .Values.global.domain }}
{{- end -}}
{{- end -}}

{{/*
Compute the default OAuth Redirect URI
*/}}
{{- define "securo.oauthRedirectUri" -}}
{{ include "securo.frontendUrl" . }}/oauth/callback
{{- end -}}
