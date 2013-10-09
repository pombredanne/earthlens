from django.views.generic import TemplateView, DetailView
from django.db.models import F, Count
from django.contrib.auth.models import User
from django.http import HttpResponseRedirect, Http404
from core.models import Image, ImageVote, Tag, UserTag


def series_queryset(series):
    if series == "index":
        return Image.objects.exclude(group_hides=True).order_by("-rating", "-votes", "id")
    elif series.startswith("m-"):
        return Image.objects.exclude(group_hides=True).filter(mission__code__iexact=series[2:]).order_by("-rating", "-votes", "id")
    elif series.startswith("mt-"):
        return Image.objects.filter(mission__code__iexact=series[3:]).order_by("date", "code")
    elif series.startswith("t-"):
        return Image.objects.exclude(group_hides=True).filter(tag_objects__tagged__slug=series[2:]).order_by("-rating", "-votes", "id")
    elif series.startswith("tt-"):
        return Image.objects.filter(tag_objects__tagged__slug=series[3:]).order_by("date", "code")
    else:
        raise ValueError("Unknown series %s" % series)


class IndexView(TemplateView):

    row_pattern = [5, 4, 5, 5, 4]
    page_size = 23
    series = "index"

    def get_template_names(self):
        if self.request.GET.get("offset", 0):
            return ["_index_rows.html"]
        else:
            return ["index.html"]

    def get_queryset(self):
        return series_queryset(self.series)

    def get_context_data(self, **kwargs):
        context = super(IndexView, self).get_context_data(**kwargs)
        offset = int(self.request.GET.get("offset", 0))
        images = list(self.get_queryset()[offset:offset+self.page_size])
        for i, image in enumerate(images):
            image.index = offset + i
        context["rows"] = self.make_rows(images)
        if not context["rows"] or not context["rows"][0]:
            raise Http404("No images left")
        context["page_size"] = self.page_size
        context["series"] = self.series
        return context

    def make_rows(self, images):
        rows = [[]]
        for image in images:
            if len(rows[-1]) >= self.row_pattern[(len(rows) - 1) % len(self.row_pattern)]:
                rows.append([])
            rows[-1].append(image)
        if not rows[-1]:
            rows = rows[:-1]
        return rows


class MissionView(IndexView):

    @property
    def series(self):
        return "m-" + self.kwargs['mission']


class MissionTimelineView(IndexView):

    row_pattern = [4]
    page_size = 20

    @property
    def series(self):
        return "mt-" + self.kwargs['mission']


class TagView(IndexView):

    @property
    def series(self):
        return "t-" + self.kwargs['slug']


class ImageView(DetailView):

    template_name = "image.html"
    model = Image

    def get_template_names(self):
        if self.request.GET.get("ajax", False):
            return ["_large_image.html"]
        else:
            return ["image.html"]

    def get_context_data(self, *args, **kwargs):
        context = super(ImageView, self).get_context_data(*args, **kwargs)
        series = self.request.GET.get('series', None)
        index = int(self.request.GET.get("index", -1))
        if series and index >= 0:
            # This is part of a series; make previous/next links
            queryset = series_queryset(series)
            if index > 0:
                context['previous_image_url'] = "/image/%s/?ajax=1&series=%s&index=%s" % (queryset[index - 1].id, series, index - 1)
            if index < queryset.count() - 1:
                context['next_image_url'] = "/image/%s/?ajax=1&series=%s&index=%s" % (queryset[index + 1].id, series, index + 1)
        return context

    def post(self, request, pk, **kwargs):

        if "group" in self.request.POST:
            in_group = self.request.POST['group'] == "true"
            Image.objects.filter(pk=pk).update(in_group=in_group)
        else:
            if "good" in self.request.POST:
                rating = 1
            elif "bad" in self.request.POST:
                rating = -1
            elif "awesome" in self.request.POST:
                rating = 3
            else:
                rating = 0

            try:
                vote = ImageVote.objects.get(user=request.user, image__pk=pk)
            except ImageVote.DoesNotExist:
                Image.objects.filter(pk=pk).update(
                    rating = F("rating") + rating,
                    votes = F("votes") + 1,
                )
                ImageVote.objects.create(user=request.user, image_id=pk, vote=rating)
            else:
                Image.objects.filter(pk=pk).update(
                    rating = F("rating") + (rating - vote.vote),
                )

        return HttpResponseRedirect(self.request.POST.get("next", "."))


class RateView(TemplateView):

    template_name = "rate.html"

    def get_context_data(self):
        if "image" in self.request.GET:
            image = Image.objects.get(pk=self.request.GET['image'])
        else:
            try:
                image = Image.objects.exclude(vote_objects__user=self.request.user).order_by("?")[0]
            except IndexError:
                return {"image": None}
        return {
            "image": image,
            "prev_image": Image.objects.get(pk=self.request.GET['prev']) if "prev" in self.request.GET else None,
        }


class TaggerView(TemplateView):

    template_name = "tagger.html"
    model = Image

    def get_context_data(self):
        if "image" in self.request.GET:
            image = Image.objects.get(pk=self.request.GET['image'])
        else:
            try:
                image = Image.objects.exclude(tag_objects__user=self.request.user).order_by("-rating")[0]
            except IndexError:
                return {"image": None}
        return {
            "tags": sorted(Tag.objects.order_by("slug"), key=lambda x: 2 if x.slug == "skip" else 1),
            "image": image,
            "prev_image": Image.objects.get(pk=self.request.GET['prev']) if "prev" in self.request.GET else None,
        }

    def post(self, request, **kwargs):
        tag = Tag.objects.get(name=self.request.POST["tag"].split("(")[0][:-1])
        image = Image.objects.get(pk=self.request.POST["image"])
        user_tag = UserTag.objects.get_or_create(user=request.user, image=image, defaults={"tagged": tag})[0]
        user_tag.tagged = tag
        user_tag.save()
        return HttpResponseRedirect(self.request.POST.get("next", "."))


class LeaderboardView(TemplateView):

    template_name = "leaderboard.html"

    def get_context_data(self):
        table = []
        vote_names = {0: "neutral", -1: "bad", 1: "good", 3: "awesome"}
        for user in User.objects.all():
            total = float(ImageVote.objects.filter(user=user).count())
            if total:
                votes = {vote_names[vote]: round(ImageVote.objects.filter(user=user, vote=vote).count()/total*100, 2) for vote in [0, 1, -1, 3]}
                votes["total"] = int(total)
                votes["tags"] = UserTag.objects.filter(user=user).count()
                votes["username"] = user.username
                table.append(votes)
        sort_key = self.request.GET.get("sort", "total")
        descending = self.request.GET.get("descending", "true") is "true"
        table.sort(key=lambda x: x.get(sort_key, None), reverse=descending)
        return {
            "table": table,
            "sort_key": sort_key,
            "descending": descending,
        }