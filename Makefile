IMAGES := $(shell docker images -f "dangling=true" -q)
CONTAINERS := $(shell docker ps -a -q -f status=exited)
VOLUME := md-zip_fs
VERSION := 0.1
REPOSITORY := local
IMAGE := md-zip_fs

ifneq (,$(wildcard .env))
    include .env
    export
endif

ifeq ($(MD_PROJECT_PATH),)
    $(error MD_PROJECT_PATH is not set. Please set it in .env file or environment)
endif



print-env:
	@echo "MD_PROJECT_PATH: $(MD_PROJECT_PATH)"

clean:
	docker rm -f $(CONTAINERS)
	docker rmi -f $(IMAGES)

build:
	docker build -t $(REPOSITORY)/messydesk/$(IMAGE):$(VERSION) .

start:
	docker run -d --name $(IMAGE) \
		-p 9003:9003 \
		-v $(MD_PROJECT_PATH)/data/:/app/data:Z \
		--restart unless-stopped \
		$(REPOSITORY)/messydesk/$(IMAGE):$(VERSION)

restart:
	docker stop $(IMAGE)
	docker rm $(IMAGE)
	$(MAKE) start

bash:
	docker exec -it $(IMAGE) bash

