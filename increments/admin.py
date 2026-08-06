from django.contrib import admin
from increments.models import IncrementRequest, IncrementFeedback, IncrementCycleSkip
from increments.models import IncrementRequest, IncrementFeedback, IncrementCycleSkip, CooReport

admin.site.register(CooReport)
admin.site.register(IncrementRequest)
admin.site.register(IncrementFeedback)
admin.site.register(IncrementCycleSkip)