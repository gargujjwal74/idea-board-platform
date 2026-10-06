{{- define "ib.fullname" -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{- define "ib.labels" -}}
app.kubernetes.io/name: idea-board
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "ib.selector" -}}
app.kubernetes.io/name: idea-board
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "ib.secretName" -}}
{{- if .Values.database.existingSecret -}}{{ .Values.database.existingSecret }}{{- else -}}{{ include "ib.fullname" . }}-db{{- end -}}
{{- end -}}

{{- define "ib.dbUrl" -}}
{{- if .Values.postgres.enabled -}}
postgresql://{{ .Values.postgres.user }}:{{ .Values.postgres.password }}@{{ include "ib.fullname" . }}-postgres:5432/{{ .Values.postgres.db }}
{{- else -}}
{{- required "Set database.url, database.existingSecret, or postgres.enabled=true" .Values.database.url -}}
{{- end -}}
{{- end -}}

{{- define "ib.securityContext" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
{{- end -}}
