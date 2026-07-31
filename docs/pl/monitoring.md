# Monitorowanie

[English](../en/monitoring.md) · [Українська](../uk/monitoring.md) · [Русский](../ru/monitoring.md) · **Polski**

Panel zbiera CPU, pamięć, dysk, sieć i stan health check. Metryki projektu są dostępne przez `GET /api/v1/monitoring/projects/{project_id}/latest`, a ostatnie metryki dostępnych projektów przez `GET /api/v1/monitoring/projects/latest`.

Przy włączonej polityce zasobów próbka zawiera limity i naruszenia. Przekroczenie tworzy powiadomienie bez duplikatów. Brak metryk oznacza „Brak danych”, a nie błąd projektu.

