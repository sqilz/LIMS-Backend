from django.contrib import admin

from .models import Project, Product, Comment, WorkLog, Animal, StudyGroup, Container, Experiment

admin.site.register(Project)
admin.site.register(Product)
admin.site.register(Comment)
admin.site.register(WorkLog)
admin.site.register(Animal)
admin.site.register(StudyGroup)
admin.site.register(Container)
admin.site.register(Experiment)

